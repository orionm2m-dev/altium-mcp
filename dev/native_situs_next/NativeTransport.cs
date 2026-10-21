// Candidate native transport. Compile/read-only file tests do not qualify dispatch.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Text.RegularExpressions;
using DXP;

namespace NativeSitusNext
{
    public static class NativeTransport
    {
        public const int MaxRequestBytes = 65536;
        private static readonly HashSet<string> Actions = new HashSet<string>(StringComparer.Ordinal) {
            "read_settings", "set_settings" };
        private static readonly HashSet<string> Reads = new HashSet<string>(StringComparer.Ordinal) {
            "read_settings" };
        private static readonly HashSet<string> Fields = new HashSet<string>(StringComparer.Ordinal) {
            "schema_version", "request_id", "action", "pcb_path", "pid", "creation_filetime", "exe", "payload" };

        // Register this signature under NativeSitusControl:ExecuteRequest, with a
        // fixed typed dispatcher. Never pass a process name or source code from JSON.
        public static void Execute(IServerDocumentView context, ref string parameters,
            Func<string, JsonElement, IServerDocumentView, object> dispatcher)
        {
            string responsePath = null, attemptPath = null, requestHash = null;
            string requestId = null, action = null, pcb = null, creation = null, executable = null;
            int pid = 0;
            bool invoked = false, claimed = false, mayMutate = false;
            object output;
            try
            {
                ParameterList parsed = new ParameterList(parameters ?? "");
                if (parsed.Count != 2) throw new ArgumentException("Exactly Request and Sha256 parameters required.");
                string encoded = parsed.GetParameterValue("Request");
                string expectedHash = parsed.GetParameterValue("Sha256");
                if (encoded == null || !Regex.IsMatch(encoded, "^[A-Za-z0-9_-]{1,2048}$") ||
                    expectedHash == null || !Regex.IsMatch(expectedHash, "^[0-9a-f]{64}$"))
                    throw new ArgumentException("Invalid request token or SHA256.");
                string b64 = encoded.Replace('-', '+').Replace('_', '/');
                b64 += new string('=', (4 - b64.Length % 4) % 4);
                string requestPath = LocalPath(new UTF8Encoding(false, true).GetString(Convert.FromBase64String(b64)));
                if (!String.Equals(Path.GetFileName(requestPath), "request.json", StringComparison.Ordinal))
                    throw new ArgumentException("Fixed request.json filename required.");
                string directory = Path.GetDirectoryName(requestPath);
                RejectReparsePoints(requestPath);
                byte[] bytes;
                // Deny concurrent write/delete until parsing and hash validation finish.
                using (FileStream input = new FileStream(requestPath, FileMode.Open, FileAccess.Read, FileShare.Read))
                {
                    if (input.Length < 2 || input.Length > MaxRequestBytes) throw new ArgumentException("Request exceeds bounded size.");
                    bytes = new byte[(int)input.Length];
                    int count = 0, read;
                    while (count < bytes.Length && (read = input.Read(bytes, count, bytes.Length-count)) > 0) count += read;
                    if (count != bytes.Length) throw new IOException("Incomplete request file.");
                }
                requestHash = Convert.ToHexString(SHA256.HashData(bytes)).ToLowerInvariant();
                if (!String.Equals(requestHash, expectedHash, StringComparison.Ordinal)) throw new ArgumentException("Request SHA256 mismatch.");
                using (JsonDocument json = JsonDocument.Parse(bytes, new JsonDocumentOptions { MaxDepth = 24 }))
                {
                    JsonElement root = json.RootElement;
                    RejectDuplicateKeys(root);
                    if (root.ValueKind != JsonValueKind.Object) throw new ArgumentException("Request must be an object.");
                    HashSet<string> seen = new HashSet<string>(StringComparer.Ordinal);
                    foreach (JsonProperty field in root.EnumerateObject()) {
                        if (!Fields.Contains(field.Name)) throw new ArgumentException("Unknown envelope field: " + field.Name);
                        seen.Add(field.Name);
                    }
                    if (!seen.SetEquals(Fields) || root.GetProperty("schema_version").GetInt32() != 1)
                        throw new ArgumentException("Complete schema version 1 envelope required.");
                    requestId = root.GetProperty("request_id").GetString();
                    if (requestId == null || !Regex.IsMatch(requestId, "^[0-9a-f]{32}$") ||
                        !String.Equals(Path.GetFileName(directory), requestId, StringComparison.Ordinal))
                        throw new ArgumentException("Nonce must match the unique request directory.");
                    action = root.GetProperty("action").GetString();
                    pcb = LocalPath(root.GetProperty("pcb_path").GetString());
                    executable = LocalPath(root.GetProperty("exe").GetString());
                    if (!String.Equals(pcb, root.GetProperty("pcb_path").GetString(), StringComparison.OrdinalIgnoreCase) ||
                        !String.Equals(executable, root.GetProperty("exe").GetString(), StringComparison.OrdinalIgnoreCase))
                        throw new ArgumentException("Canonical envelope paths required.");
                    pid = root.GetProperty("pid").GetInt32();
                    creation = root.GetProperty("creation_filetime").GetString();
                    long filetime;
                    if (!String.Equals(Path.GetExtension(pcb), ".PcbDoc", StringComparison.OrdinalIgnoreCase) || pid <= 0 ||
                        !Int64.TryParse(creation, NumberStyles.None, CultureInfo.InvariantCulture, out filetime) || filetime <= 0 ||
                        root.GetProperty("payload").ValueKind != JsonValueKind.Object)
                        throw new ArgumentException("Exact PCB, PID, creation time and object payload required.");
                    responsePath = Path.Combine(directory, "response.json");
                    attemptPath = Path.Combine(directory, "attempt.json");
                    // Existing attempt OR result is terminal for this nonce, including crashes.
                    if (File.Exists(attemptPath) || File.Exists(responsePath) || File.Exists(responsePath + ".pending")) {
                        responsePath = null;
                        throw new InvalidOperationException("Request already attempted; no replay.");
                    }
                    if (!Actions.Contains(action)) throw new ArgumentException("Unsupported fixed action.");
                    if (dispatcher == null) throw new ArgumentException("Fixed dispatcher unavailable.");
                    mayMutate = !Reads.Contains(action);
                    // Claim first: only this nonce owner may publish even a refusal.
                    // Otherwise a pre-claim error could race a successful dispatcher.
                    WriteNew(attemptPath, new { schema_version=1, request_id=requestId, action, request_sha256=requestHash,
                        pcb_path=pcb, pid, creation_filetime=creation, exe=executable, dispatch_attempted=true,
                        mutation_possible=mayMutate, retry_allowed=false, document_saved=false });
                    claimed = true;
                    // Revalidate immediately before entry, not only before writing the durable claim.
                    Verify(context, pcb, pid, filetime, executable);
                    invoked = true;
                    object data = dispatcher(action, root, context);
                    Verify(context, pcb, pid, filetime, executable);
                    output = new { schema_version=1, success=true, request_id=requestId, action, request_sha256=requestHash,
                        pcb_path=pcb, pid, creation_filetime=creation, exe=executable, native_handler_completed=true,
                        operation_invoked=true, mutation_possible=mayMutate, outcome_unknown=false,
                        retry_allowed=false, document_saved=false, data };
                }
            }
            catch (Exception error)
            {
                // Another entry may have won CreateNew between existence check
                // and claim. It owns the receipt; never publish our error there.
                if (!claimed && attemptPath != null && File.Exists(attemptPath)) responsePath = null;
                output = new { schema_version=1, success=false, request_id=requestId, action, request_sha256=requestHash,
                    pcb_path=pcb, pid, creation_filetime=creation, exe=executable, native_handler_completed=false,
                    operation_invoked=invoked, attempt_claimed=claimed, mutation_possible=mayMutate && invoked,
                    outcome_unknown=invoked, retry_allowed=false, document_saved=false, error=error.Message };
            }
            parameters = JsonSerializer.Serialize(output);
            if (responsePath != null && claimed)
            {
                try {
                    // A partial write never looks like a final response. Never overwrite either file.
                    WriteNew(responsePath + ".pending", output);
                    File.Move(responsePath + ".pending", responsePath);
                } catch (Exception error) {
                    parameters = JsonSerializer.Serialize(new { success=false, request_id=requestId,
                        outcome_unknown=invoked, retry_allowed=false, error="Receipt write failed: " + error.Message });
                }
            }
        }

        private static string LocalPath(string value)
        {
            if (String.IsNullOrWhiteSpace(value) || value.Length > 1024 || !Regex.IsMatch(value, "^[A-Za-z]:\\\\") ||
                value.IndexOfAny(new char[] {'\0', '\r', '\n', '|', '"', '<', '>'}) >= 0 || value.Substring(2).Contains(":"))
                throw new ArgumentException("Absolute local Windows path required.");
            return Path.GetFullPath(value);
        }
        private static void RejectReparsePoints(string file)
        {
            for (string part = file; !String.IsNullOrEmpty(part); part = Path.GetDirectoryName(part))
                if ((File.GetAttributes(part) & FileAttributes.ReparsePoint) != 0)
                    throw new ArgumentException("Reparse points are not accepted for request exchange.");
        }
        private static void RejectDuplicateKeys(JsonElement element)
        {
            if (element.ValueKind == JsonValueKind.Object) {
                HashSet<string> keys = new HashSet<string>(StringComparer.Ordinal);
                foreach (JsonProperty property in element.EnumerateObject()) {
                    if (!keys.Add(property.Name)) throw new ArgumentException("Duplicate JSON key.");
                    RejectDuplicateKeys(property.Value);
                }
            } else if (element.ValueKind == JsonValueKind.Array)
                foreach (JsonElement item in element.EnumerateArray()) RejectDuplicateKeys(item);
        }
        private static void Verify(IServerDocumentView context, string pcb, int pid, long filetime, string executable)
        {
            using (Process process = Process.GetCurrentProcess())
                if (process.Id != pid || process.StartTime.ToUniversalTime().ToFileTimeUtc() != filetime ||
                    !String.Equals(process.MainModule.FileName, executable, StringComparison.OrdinalIgnoreCase))
                    throw new InvalidOperationException("Exact native process identity mismatch.");
            IClient client = DXP.GlobalVars.Client;
            IServerDocumentView active = client == null ? null : client.GetCurrentView();
            IServerDocument current = active == null ? null : active.GetOwnerDocument();
            IServerDocument target = context == null ? null : context.GetOwnerDocument();
            foreach (IServerDocument document in new [] {current, target})
                if (document == null || !String.Equals(document.GetKind(), "PCB", StringComparison.OrdinalIgnoreCase) ||
                    !String.Equals(document.GetFileName(), pcb, StringComparison.OrdinalIgnoreCase))
                    throw new InvalidOperationException("Exact active/context PCB mismatch.");
        }
        private static void WriteNew(string path, object value)
        {
            byte[] bytes = new UTF8Encoding(false).GetBytes(JsonSerializer.Serialize(value));
            using (FileStream file = new FileStream(path, FileMode.CreateNew, FileAccess.Write, FileShare.Read)) {
                file.Write(bytes, 0, bytes.Length); file.Flush(true);
            }
        }
    }
}
