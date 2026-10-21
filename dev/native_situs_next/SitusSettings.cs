// Typed active-board Situs settings. Compile-only until separately qualified.
// No file, process, UI, routing, save, preset-library or arbitrary parameter I/O.
using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using System.Security.Cryptography;
using System.Text.Json;
using DXP;

namespace NativeSitusNext
{
    public sealed class StrategySnapshot
    {
        public string name { get; set; }
        public string description { get; set; }
        public string passes { get; set; }
        public int via_bias { get; set; }
        public bool prefer_orthogonal { get; set; }
    }
    public sealed class DirectionSnapshot
    {
        public uint layer_id { get; set; }
        public string layer_name { get; set; }
        public bool layer_used { get; set; }
        public int direction { get; set; }
        public string direction_name { get; set; }
    }
    public sealed class SettingsSnapshot
    {
        public int schema_version { get; set; } = 1;
        public string pcb_path { get; set; }
        public string board_identity { get; set; }
        public bool strategy_valid { get; set; }
        public StrategySnapshot active_strategy { get; set; }
        public List<DirectionSnapshot> layer_directions { get; set; }
        public bool global_lock_preroutes { get; set; }
        public string lock_preroutes_scope { get; set; } = "global_pcb_system_options";
    }

    // Narrow seam for deterministic offline compare/readback tests. The native
    // implementation below is the only implementation used by Dispatch.
    internal interface ISettingsSurface
    {
        SettingsSnapshot Read();
        void SetName(string value);
        void SetDescription(string value);
        void SetPreferOrthogonal(bool value);
        void SetDirection(uint layerId, int direction);
    }

    public static class SitusSettings
    {
        internal static readonly string[] DirectionNames = {
            "eRLLayerNotUsed", "eRLRouteHorizontal", "eRLRouteVertical", "eRLRouteSingleLayer",
            "eRLRoute_1_OClock", "eRLRoute_2_OClock", "eRLRoute_4_OClock", "eRLRoute_5_OClock",
            "eRLRoute_45_Up", "eRLRoute_45_Down", "eRLRouteFanout", "eRLRouteAuto" };

        // Transport validates root identity and creates its durable attempt
        // marker before invoking set_settings. All settings fields are payload.
        public static object Dispatch(string action, JsonElement request, IServerDocumentView context)
        {
            if (action != "read_settings" && action != "set_settings")
                throw new ArgumentException("Unsupported settings action.");
            string pcb = request.GetProperty("pcb_path").GetString();
            JsonElement payload = request.GetProperty("payload");
            if (action == "read_settings")
            {
                ObjectFields(payload, new string[0]);
                SettingsSnapshot state = new NativeSurface(context, pcb).Read();
                return new { success = true, action, snapshot = state, snapshot_hash = Hash(state),
                    mutation_attempted = false, readback_verified = true, outcome_unknown = false,
                    runtime_qualified = false, persistence_verified = false };
            }
            // Parse every input before obtaining the native surface or writing.
            ParsedChange change = ParseChange(payload);
            return Apply(new NativeSurface(context, pcb), change);
        }

        internal static string Hash(SettingsSnapshot snapshot)
        {
            byte[] bytes = JsonSerializer.SerializeToUtf8Bytes(snapshot);
            using (SHA256 sha = SHA256.Create())
                return Convert.ToHexString(sha.ComputeHash(bytes)).ToLowerInvariant();
        }
        internal static SettingsSnapshot Copy(SettingsSnapshot value)
        {
            return JsonSerializer.Deserialize<SettingsSnapshot>(JsonSerializer.Serialize(value));
        }
        private static void ObjectFields(JsonElement value, params string[] allowed)
        {
            if (value.ValueKind != JsonValueKind.Object) throw new ArgumentException("JSON object required.");
            var seen = new HashSet<string>(StringComparer.Ordinal);
            foreach (JsonProperty property in value.EnumerateObject())
                if (!seen.Add(property.Name) || !allowed.Contains(property.Name, StringComparer.Ordinal))
                    throw new ArgumentException("Duplicate, unsupported or unqualified field: " + property.Name);
        }
        private static string Text(JsonElement value, string field, int minimum, int maximum)
        {
            if (value.ValueKind != JsonValueKind.String) throw new ArgumentException(field + " must be a string.");
            string text = value.GetString();
            if (text == null || text.Length < minimum || text.Length > maximum || text.IndexOf('\0') >= 0 ||
                (minimum > 0 && String.IsNullOrWhiteSpace(text)))
                throw new ArgumentException(field + " exceeds the tool text bounds or contains NUL.");
            return text;
        }
        internal sealed class ParsedChange
        {
            public string ExpectedHash;
            public string Name;
            public string Description;
            public bool? PreferOrthogonal;
            public readonly SortedDictionary<uint, int> Directions = new SortedDictionary<uint, int>();
        }
        internal static ParsedChange ParseChange(JsonElement payload)
        {
            ObjectFields(payload, "expected_snapshot_hash", "changes");
            string hash = Text(payload.GetProperty("expected_snapshot_hash"), "expected_snapshot_hash", 64, 64);
            if (hash.Any(c => !((c >= '0' && c <= '9') || (c >= 'a' && c <= 'f'))))
                throw new ArgumentException("A lowercase SHA-256 snapshot hash is required.");
            JsonElement changes = payload.GetProperty("changes");
            ObjectFields(changes, "name", "description", "prefer_orthogonal", "layer_directions");
            if (!changes.EnumerateObject().Any()) throw new ArgumentException("At least one explicit change is required.");
            var parsed = new ParsedChange { ExpectedHash = hash };
            foreach (JsonProperty property in changes.EnumerateObject())
            {
                switch (property.Name)
                {
                    case "name": parsed.Name = Text(property.Value, "name", 1, 256); break;
                    case "description": parsed.Description = Text(property.Value, "description", 0, 4096); break;
                    case "prefer_orthogonal":
                        if (property.Value.ValueKind != JsonValueKind.True && property.Value.ValueKind != JsonValueKind.False)
                            throw new ArgumentException("prefer_orthogonal must be boolean.");
                        parsed.PreferOrthogonal = property.Value.GetBoolean(); break;
                    case "layer_directions":
                        if (property.Value.ValueKind != JsonValueKind.Array || property.Value.GetArrayLength() < 1 || property.Value.GetArrayLength() > 256)
                            throw new ArgumentException("layer_directions must contain 1..256 entries.");
                        foreach (JsonElement layer in property.Value.EnumerateArray())
                        {
                            ObjectFields(layer, "layer_id", "direction");
                            uint id; int direction;
                            if (!layer.GetProperty("layer_id").TryGetUInt32(out id) ||
                                !layer.GetProperty("direction").TryGetInt32(out direction) || direction < 0 || direction >= DirectionNames.Length ||
                                parsed.Directions.ContainsKey(id))
                                throw new ArgumentException("Unique native layer IDs and known direction integers 0..11 required.");
                            parsed.Directions.Add(id, direction);
                        }
                        break;
                }
            }
            return parsed;
        }
        internal static object Apply(ISettingsSurface surface, ParsedChange change)
        {
            SettingsSnapshot before = null, after = null;
            var attempts = new List<string>();
            try
            {
                before = Copy(surface.Read());
                if (Hash(before) != change.ExpectedHash) throw new InvalidOperationException("stale_expected_snapshot");
                if (!before.strategy_valid) throw new InvalidOperationException("current_strategy_is_not_valid");
                SettingsSnapshot desired = Copy(before);
                if (change.Name != null) desired.active_strategy.name = change.Name;
                if (change.Description != null) desired.active_strategy.description = change.Description;
                if (change.PreferOrthogonal.HasValue) desired.active_strategy.prefer_orthogonal = change.PreferOrthogonal.Value;
                foreach (var pair in change.Directions)
                {
                    DirectionSnapshot layer = desired.layer_directions.SingleOrDefault(x => x.layer_id == pair.Key);
                    if (layer == null || !layer.layer_used) throw new InvalidOperationException("requested_layer_is_not_an_active_native_signal_layer");
                    layer.direction = pair.Value; layer.direction_name = DirectionNames[pair.Value];
                }
                SettingsSnapshot working = Copy(before);
                Action<string, Action, Action> applyOne = (name, setter, updateExpected) => {
                    after = Copy(surface.Read());
                    if (Hash(after) != Hash(working)) throw new InvalidOperationException("settings_changed_before_setter");
                    attempts.Add(name); // Before call: a thrown setter has unknown effects.
                    setter(); updateExpected();
                    after = Copy(surface.Read());
                    if (Hash(after) != Hash(working)) throw new InvalidOperationException("full_snapshot_readback_mismatch");
                };
                if (desired.active_strategy.name != working.active_strategy.name)
                    applyOne("name", () => surface.SetName(desired.active_strategy.name), () => working.active_strategy.name = desired.active_strategy.name);
                if (desired.active_strategy.description != working.active_strategy.description)
                    applyOne("description", () => surface.SetDescription(desired.active_strategy.description), () => working.active_strategy.description = desired.active_strategy.description);
                if (desired.active_strategy.prefer_orthogonal != working.active_strategy.prefer_orthogonal)
                    applyOne("prefer_orthogonal", () => surface.SetPreferOrthogonal(desired.active_strategy.prefer_orthogonal), () => working.active_strategy.prefer_orthogonal = desired.active_strategy.prefer_orthogonal);
                foreach (DirectionSnapshot wanted in desired.layer_directions)
                {
                    DirectionSnapshot current = working.layer_directions.Single(x => x.layer_id == wanted.layer_id);
                    if (wanted.direction != current.direction)
                        applyOne("layer_direction:" + wanted.layer_id.ToString(CultureInfo.InvariantCulture),
                            () => surface.SetDirection(wanted.layer_id, wanted.direction),
                            () => { current.direction = wanted.direction; current.direction_name = wanted.direction_name; });
                }
                after = Copy(surface.Read());
                if (Hash(after) != Hash(desired)) throw new InvalidOperationException("final_snapshot_readback_mismatch");
                return Outcome(true, before, after, attempts, "");
            }
            catch (Exception error)
            {
                // No automatic rollback or replay. Previously observed after may
                // predate a throwing setter, so discard it and obtain fresh data.
                after = null;
                try { after = Copy(surface.Read()); } catch (Exception) { }
                return Outcome(false, before, after, attempts, error.Message);
            }
        }
        private static object Outcome(bool success, SettingsSnapshot before, SettingsSnapshot after, List<string> attempts, string error)
        {
            bool? changed = before != null && after != null ? Hash(before) != Hash(after) : (bool?)null;
            return new { success, action = "set_settings", before, after,
                before_hash = before == null ? null : Hash(before), after_hash = after == null ? null : Hash(after),
                mutation_attempted = attempts.Count > 0, mutation_steps_attempted = attempts.ToArray(), changed,
                already_satisfied = success && attempts.Count == 0, readback_verified = success,
                outcome_unknown = !success && attempts.Count > 0, error, retry_allowed = false,
                rollback_attempted = false, atomicity_verified = false, undo_verified = false,
                persistence_verified = false, runtime_qualified = false };
        }

        private sealed class NativeSurface : ISettingsSurface
        {
            private readonly IServerDocumentView context;
            private readonly string pcb;
            private readonly IntPtr boardIdentity;
            private sealed class NativeObjects
            {
                public RT_PCB.IPCB_Board Board;
                public PCBInterfaces.IPCB_Board_SaveLoadParameters Owner;
                public PCBInterfaces.IPCB_SitusStrategy Strategy;
                public PCB.IPCB_SystemOptions Options;
            }
            public NativeSurface(IServerDocumentView context, string expectedPcb)
            {
                this.context = context; pcb = expectedPcb;
                NativeObjects native = Acquire(false);
                boardIdentity = native.Board.I_ObjectAddress();
                if (boardIdentity == IntPtr.Zero) throw new InvalidOperationException("Native board identity is unavailable.");
            }
            private NativeObjects Acquire(bool compareIdentity = true)
            {
                var client = DXP.GlobalVars.Client;
                if (client == null || context == null) throw new InvalidOperationException("Native context unavailable.");
                IServerDocumentView current = client.GetCurrentView();
                foreach (IServerDocumentView view in new[] { current, context })
                {
                    IServerDocument doc = view == null ? null : view.GetOwnerDocument();
                    if (doc == null || !String.Equals(doc.GetFileName(), pcb, StringComparison.OrdinalIgnoreCase) ||
                        !String.Equals(doc.GetKind(), "PCB", StringComparison.OrdinalIgnoreCase))
                        throw new InvalidOperationException("Exact active/context PCB changed.");
                }
                var pcbModule = client.Internal_GetServerModuleByName("PCB") as DXP.IServerModule;
                var clientControl = client.Internal_GetProcessControl() as DXP.IProcessControl;
                var pcbControl = pcbModule == null ? null : pcbModule.Internal_GetProcessControl() as DXP.IProcessControl;
                if (clientControl == null || pcbControl == null || clientControl.GetProcessDepth() != 0 || pcbControl.GetProcessDepth() != 0)
                    throw new InvalidOperationException("Native Client/PCB process is not idle.");
                var server = PCB.GlobalVars.PCBServer;
                object raw = server == null ? null : server.Internal_GetCurrentPCBBoard();
                var board = raw as RT_PCB.IPCB_Board;
                var owner = raw as PCBInterfaces.IPCB_Board_SaveLoadParameters;
                if (board == null || owner == null || !String.Equals(board.GetState_FileName(), pcb, StringComparison.OrdinalIgnoreCase) ||
                    (compareIdentity && board.I_ObjectAddress() != boardIdentity))
                    throw new InvalidOperationException("Exact typed native PCB identity/owner unavailable or changed.");
                var strategy = owner.GetState_SitusStrategy();
                var options = server.Internal_GetState_SystemOptions() as PCB.IPCB_SystemOptions;
                if (strategy == null || options == null) throw new InvalidOperationException("Typed strategy or global SystemOptions unavailable.");
                return new NativeObjects { Board = board, Owner = owner, Strategy = strategy, Options = options };
            }
            private static SortedDictionary<uint, Basic.TV7_Layer> Layers(RT_PCB.IPCB_Board board)
            {
                var layers = new SortedDictionary<uint, Basic.TV7_Layer>();
                RT_PCB.IPCB_LayerIterator iterator = board.SignalLayerIterator();
                if (iterator == null) throw new InvalidOperationException("Native signal-layer iterator unavailable.");
                bool more = iterator.First();
                while (more)
                {
                    Basic.TV7_Layer layer = iterator.Layer();
                    if (layers.Count >= 256 || layers.ContainsKey(layer.ID)) throw new InvalidOperationException("Native layer enumeration is unbounded or duplicated.");
                    layers.Add(layer.ID, layer); more = iterator.Next();
                }
                if (layers.Count == 0) throw new InvalidOperationException("No native signal layers were enumerated.");
                return layers;
            }
            public SettingsSnapshot Read()
            {
                NativeObjects native = Acquire();
                var result = new SettingsSnapshot { pcb_path = pcb, board_identity = boardIdentity.ToInt64().ToString(CultureInfo.InvariantCulture),
                    strategy_valid = native.Owner.SitusStrategyValid(), global_lock_preroutes = native.Options.GetState_LockPreRoutes(),
                    active_strategy = new StrategySnapshot { name = native.Strategy.GetState_Name(), description = native.Strategy.GetState_Description(),
                        passes = native.Strategy.GetState_Passes(), via_bias = native.Strategy.GetState_ViaBias(), prefer_orthogonal = native.Strategy.GetState_PreferOrthogonal() },
                    layer_directions = new List<DirectionSnapshot>() };
                foreach (var pair in Layers(native.Board))
                {
                    int direction = (int)native.Owner.GetState_RoutingLayerDirections(pair.Value);
                    if (direction < 0 || direction >= DirectionNames.Length) throw new InvalidOperationException("Unrecognized native routing direction.");
                    result.layer_directions.Add(new DirectionSnapshot { layer_id = pair.Key, layer_name = native.Board.LayerName(pair.Value),
                        layer_used = native.Board.GetState_LayerIsUsed(pair.Value), direction = direction, direction_name = DirectionNames[direction] });
                }
                Acquire();
                return result;
            }
            public void SetName(string value) { Acquire().Strategy.SetState_Name(value); }
            public void SetDescription(string value) { Acquire().Strategy.SetState_Description(value); }
            public void SetPreferOrthogonal(bool value) { Acquire().Strategy.SetState_PreferOrthogonal(value); }
            public void SetDirection(uint layerId, int direction)
            {
                if (direction < 0 || direction >= DirectionNames.Length) throw new ArgumentException("Unknown native direction.");
                NativeObjects native = Acquire(); Basic.TV7_Layer layer;
                if (!Layers(native.Board).TryGetValue(layerId, out layer) || !native.Board.GetState_LayerIsUsed(layer))
                    throw new InvalidOperationException("Signal layer changed before setter.");
                native.Owner.SetState_RoutingLayerDirections(layer, (RT_PCB.TRouteLayer)direction);
            }
        }
    }
}
