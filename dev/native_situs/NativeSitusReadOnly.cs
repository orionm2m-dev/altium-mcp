// Compile-only, read-only prototype. No installation or activation is performed.
using System;
using System.Diagnostics;
using System.IO;
using System.Globalization;
using System.Runtime.InteropServices;
using System.Text;
using DXP;

namespace CSharpPlugin
{
    // This bootstrap shape is observed in two installed first-party servers.
    // Loader compatibility remains unqualified until an explicit native probe.
    [ComVisible(true)]
    [ClassInterface(ClassInterfaceType.None)]
    public sealed class PluginFactory
    {
        public object InvokePluginFactory(IClient client)
        {
            if (client == null) throw new ArgumentNullException("client");
            return new NativeSitusReadOnly.Server(client);
        }
    }
}

namespace NativeSitusReadOnly
{
    public sealed class Server : ServerModule
    {
        public Server(IClient client) : base(client, "NativeSitusReadOnly") { }
        protected override IServerDocument NewDocumentInstance(string kind, string fileName) { return null; }
        protected override void InitializeCommands()
        {
            ((CommandLauncher)CommandLauncher).RegisterCommand("Heartbeat", Heartbeat, GetState);
            ((CommandLauncher)CommandLauncher).RegisterCommand("ReadCommandState", ReadCommandState, GetState);
            ((CommandLauncher)CommandLauncher).RegisterCommand("ReadBoardCapabilities", ReadBoardCapabilities, GetState);
        }
        private static void GetState(IServerDocumentView context, ref string parameters,
            ref bool enabled, ref bool isChecked, ref bool visible,
            ref string caption, ref string imageFile)
        {
            enabled = context != null; visible = true; isChecked = false;
        }

        // Exact absolute PCB | unique 32-lowercase-hex request ID | existing receipt
        // directory | expected PID | expected UTC process-creation filetime.
        private static string[] Guard(IServerDocumentView context, string parameters)
        {
            string[] input = (parameters ?? "").Split('|');
            if (input.Length != 5 || !Path.IsPathFullyQualified(input[0]) ||
                !String.Equals(Path.GetExtension(input[0]), ".PcbDoc", StringComparison.OrdinalIgnoreCase) ||
                input[1].Length != 32 || !Path.IsPathFullyQualified(input[2]) || !Directory.Exists(input[2]))
                throw new ArgumentException("Exact PCB, request ID, receipt directory, PID and process creation time required.");
            foreach (char c in input[1]) if (!(c >= '0' && c <= '9') && !(c >= 'a' && c <= 'f'))
                throw new ArgumentException("Invalid request id.");
            input[0] = Path.GetFullPath(input[0]);
            VerifyIdentity(context, input);
            input[2] = Path.Combine(Path.GetFullPath(input[2]), input[1] + ".json");
            if (File.Exists(input[2])) throw new InvalidOperationException("Receipt already exists; request will not replay.");
            return input;
        }
        private static void VerifyIdentity(IServerDocumentView context, string[] input)
        {
            int expectedPid; long expectedCreation;
            if (!Int32.TryParse(input[3], NumberStyles.None, CultureInfo.InvariantCulture, out expectedPid) ||
                !Int64.TryParse(input[4], NumberStyles.None, CultureInfo.InvariantCulture, out expectedCreation) ||
                expectedPid <= 0 || expectedCreation <= 0)
                throw new ArgumentException("Positive exact process identity required.");
            using (Process process = Process.GetCurrentProcess())
                if (process.Id != expectedPid || process.StartTime.ToUniversalTime().ToFileTimeUtc() != expectedCreation)
                    throw new InvalidOperationException("Native process identity mismatch.");
            IServerDocumentView active = DXP.GlobalVars.Client == null ? null : DXP.GlobalVars.Client.GetCurrentView();
            IServerDocument activeDocument = active == null ? null : active.GetOwnerDocument();
            if (activeDocument == null || !String.Equals(activeDocument.GetFileName(), input[0], StringComparison.OrdinalIgnoreCase) ||
                !String.Equals(activeDocument.GetKind(), "PCB", StringComparison.OrdinalIgnoreCase))
                throw new InvalidOperationException("Expected PCB is not the active native document.");
            IServerDocument document = context == null ? null : context.GetOwnerDocument();
            if (document == null || !String.Equals(document.GetFileName(), input[0], StringComparison.OrdinalIgnoreCase) ||
                !String.Equals(document.GetKind(), "PCB", StringComparison.OrdinalIgnoreCase))
                throw new InvalidOperationException("Exact native PCB document does not match.");
        }
        private static void WriteReceipt(IServerDocumentView context, string[] input, string kind, string extra, ref string parameters)
        {
            VerifyIdentity(context, input);
            parameters = "{\"success\":true,\"kind\":" + Quote(kind) + ",\"request_id\":" + Quote(input[1]) +
                ",\"pcb_path\":" + Quote(input[0]) + ",\"pid\":" + input[3] +
                ",\"process_creation_filetime\":" + Quote(input[4]) +
                ",\"routing_dispatched\":false,\"document_saved\":false,\"runtime_qualified\":false" + extra + "}";
            using (FileStream stream = new FileStream(input[2], FileMode.CreateNew, FileAccess.Write, FileShare.Read))
            using (StreamWriter writer = new StreamWriter(stream, new UTF8Encoding(false))) writer.Write(parameters);
        }
        private static void Error(Exception error, ref string parameters)
        {
            parameters = "{\"success\":false,\"routing_dispatched\":false,\"document_saved\":false,\"error\":" + Quote(error.Message) + "}";
        }
        public static void Heartbeat(IServerDocumentView context, ref string parameters)
        {
            try { WriteReceipt(context, Guard(context, parameters), "heartbeat", "", ref parameters); }
            catch (Exception error) { Error(error, ref parameters); }
        }
        public static void ReadCommandState(IServerDocumentView context, ref string parameters)
        {
            try
            {
                string[] input = Guard(context, parameters);
                StringBuilder result = new StringBuilder(",\"commands\":[");
                string[] actions = { "Start", "Setup", "Pause", "Stop", "Reset" };
                for (int i = 0; i < actions.Length; i++)
                {
                    bool enabled = false, isChecked = false, visible = false;
                    string caption = "", image = "", arguments = "Action=" + actions[i];
                    Utils.MessageRouterGetCommandState("PCB:Autoroute", ref arguments, context,
                        ref enabled, ref isChecked, ref visible, ref caption, ref image);
                    if (i != 0) result.Append(',');
                    result.Append("{\"action\":").Append(Quote(actions[i]))
                        .Append(",\"enabled\":").Append(enabled ? "true" : "false")
                        .Append(",\"checked\":").Append(isChecked ? "true" : "false")
                        .Append(",\"visible\":").Append(visible ? "true" : "false")
                        .Append(",\"caption\":").Append(Quote(caption)).Append('}');
                }
                WriteReceipt(context, input, "command_state", result.Append(']').ToString(), ref parameters);
            }
            catch (Exception error) { Error(error, ref parameters); }
        }
        public static void ReadBoardCapabilities(IServerDocumentView context, ref string parameters)
        {
            try
            {
                string[] input = Guard(context, parameters);
                var pcbServer = PCB.GlobalVars.PCBServer;
                if (pcbServer == null) throw new InvalidOperationException("PCB server unavailable.");
                object nativeBoard = pcbServer.Internal_GetCurrentPCBBoard();
                var board = nativeBoard as PCB.IPCB_Board;
                if (board == null || !String.Equals(board.GetState_FileName(), input[0], StringComparison.OrdinalIgnoreCase))
                    throw new InvalidOperationException("Current native PCB interface/path mismatch.");
                // Safe managed interface capability test only. Never reinterpret native pointers.
                // This EDP interface's metadata is installed, not a public acquisition guarantee.
                var parametersInterface = nativeBoard as PCBInterfaces.IPCB_Board_SaveLoadParameters;
                bool available = parametersInterface != null;
                string valid = available ? (parametersInterface.SitusStrategyValid() ? "true" : "false") : "null";
                WriteReceipt(context, input, "board_capabilities",
                    ",\"board_save_load_parameters_supported\":" + (available ? "true" : "false") +
                    ",\"situs_strategy_valid\":" + valid +
                    ",\"qualification_boundary\":\"Typed EDP interface capability probe only; no strategy mutation or routing\"",
                    ref parameters);
            }
            catch (Exception error) { Error(error, ref parameters); }
        }
        private static string Quote(string value)
        {
            StringBuilder result = new StringBuilder("\"");
            foreach (char c in value ?? "")
            {
                if (c == '\\' || c == '"') result.Append('\\').Append(c);
                else if (c < 32) result.Append("\\u").Append(((int)c).ToString("x4"));
                else result.Append(c);
            }
            return result.Append('"').ToString();
        }
    }
}

