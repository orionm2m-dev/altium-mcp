// Runs only fake settings surfaces. No Altium application or native object calls.
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Runtime.Loader;
using System.Text.Json;
using NativeSitusNext;

internal sealed class FakeSurface : ISettingsSurface
{
    internal SettingsSnapshot State = TestCases.Initial();
    internal readonly List<string> Writes = new List<string>();
    internal int Reads;
    internal Action<FakeSurface> OnRead;
    internal Action<FakeSurface, string> OnWrite;
    public SettingsSnapshot Read() { Reads++; OnRead?.Invoke(this); return SitusSettings.Copy(State); }
    private void Write(string field, Action update) { Writes.Add(field); update(); OnWrite?.Invoke(this, field); }
    public void SetName(string value) { Write("name", () => State.active_strategy.name = value); }
    public void SetDescription(string value) { Write("description", () => State.active_strategy.description = value); }
    public void SetPreferOrthogonal(bool value) { Write("prefer_orthogonal", () => State.active_strategy.prefer_orthogonal = value); }
    public void SetDirection(uint layer, int value) { Write("direction:" + layer, () => { var target = State.layer_directions.Single(x => x.layer_id == layer); target.direction = value; target.direction_name = SitusSettings.DirectionNames[value]; }); }
}
internal static class TestCases
{
    internal static SettingsSnapshot Initial() => new SettingsSnapshot {
        pcb_path = @"C:\qualification\scratch.PcbDoc", board_identity = "12345", strategy_valid = true,
        active_strategy = new StrategySnapshot { name = "Current", description = "", passes = "opaque pass data", via_bias = -123, prefer_orthogonal = false },
        global_lock_preroutes = false,
        layer_directions = new List<DirectionSnapshot> {
            new DirectionSnapshot { layer_id = 1001, layer_name = "Top", layer_used = true, direction = 1, direction_name = SitusSettings.DirectionNames[1] },
            new DirectionSnapshot { layer_id = 4002, layer_name = "Bottom", layer_used = true, direction = 2, direction_name = SitusSettings.DirectionNames[2] },
            new DirectionSnapshot { layer_id = 9003, layer_name = "Unused", layer_used = false, direction = 0, direction_name = SitusSettings.DirectionNames[0] } } };
    private static void Require(bool condition, string message = "Assertion failed") { if (!condition) throw new Exception(message); }
    private static JsonElement Parse(string json) { using (JsonDocument document = JsonDocument.Parse(json)) return document.RootElement.Clone(); }
    private static string Payload(FakeSurface surface, string changes) => "{\"expected_snapshot_hash\":\"" + SitusSettings.Hash(surface.State) + "\",\"changes\":" + changes + "}";
    private static JsonElement Apply(FakeSurface surface, string changes) => ApplyPayload(surface, Payload(surface, changes));
    private static JsonElement ApplyPayload(FakeSurface surface, string payload) => Parse(JsonSerializer.Serialize(SitusSettings.Apply(surface, SitusSettings.ParseChange(Parse(payload)))));
    private static bool Flag(JsonElement result, string name) => result.GetProperty(name).GetBoolean();
    private static void Reject(string changes) {
        var surface = new FakeSurface(); bool rejected = false;
        try { SitusSettings.ParseChange(Parse(Payload(surface, changes))); } catch (Exception) { rejected = true; }
        Require(rejected, "Input was accepted: " + changes); Require(surface.Writes.Count == 0);
    }
    internal static int Run()
    {
        int count = 0;
        Action<string, Action> test = (name, body) => { body(); count++; Console.WriteLine("PASS " + name); };
        test("all supported fields compare/readback", () => {
            var surface = new FakeSurface(); var result = Apply(surface, "{\"name\":\"Modified\",\"description\":\"changed\",\"prefer_orthogonal\":true,\"layer_directions\":[{\"layer_id\":4002,\"direction\":11}]}");
            Require(Flag(result,"success") && Flag(result,"readback_verified")); Require(surface.Writes.SequenceEqual(new[]{"name","description","prefer_orthogonal","direction:4002"}));
            Require(surface.State.active_strategy.passes == "opaque pass data" && surface.State.active_strategy.via_bias == -123 && !surface.State.global_lock_preroutes);
            Require(!Flag(result,"runtime_qualified") && !Flag(result,"persistence_verified") && !Flag(result,"atomicity_verified"));
        });
        test("idempotent desired value no setter", () => { var s = new FakeSurface(); var r = Apply(s,"{\"name\":\"Current\"}"); Require(Flag(r,"success") && Flag(r,"already_satisfied") && !Flag(r,"mutation_attempted") && s.Writes.Count == 0); });
        test("stale complete hash no setter", () => { var s = new FakeSurface(); string p = Payload(s,"{\"name\":\"Other\"}"); s.State.active_strategy.passes = "changed outside"; var r = ApplyPayload(s,p); Require(!Flag(r,"success") && !Flag(r,"mutation_attempted") && s.Writes.Count == 0); });
        test("invalid strategy refused", () => { var s = new FakeSurface(); s.State.strategy_valid = false; var r = Apply(s,"{\"name\":\"Other\"}"); Require(!Flag(r,"success") && s.Writes.Count == 0); });
        test("unknown native layer rejected before scalar writes", () => { var s = new FakeSurface(); var r = Apply(s,"{\"name\":\"Other\",\"layer_directions\":[{\"layer_id\":1,\"direction\":0}]}"); Require(!Flag(r,"success") && s.Writes.Count == 0); });
        test("unused native signal layer refused", () => { var s = new FakeSurface(); var r = Apply(s,"{\"layer_directions\":[{\"layer_id\":9003,\"direction\":11}]}"); Require(!Flag(r,"success") && s.Writes.Count == 0); });
        test("all twelve known directions", () => { for (int d=0;d<12;d++) { var s = new FakeSurface(); var r = Apply(s,"{\"layer_directions\":[{\"layer_id\":1001,\"direction\":"+d+"}]}"); Require(Flag(r,"success") && s.State.layer_directions[0].direction == d); } });
        test("deterministic native layer order", () => { var s = new FakeSurface(); var r = Apply(s,"{\"layer_directions\":[{\"layer_id\":4002,\"direction\":11},{\"layer_id\":1001,\"direction\":11}]}"); Require(Flag(r,"success") && s.Writes.SequenceEqual(new[]{"direction:1001","direction:4002"})); });
        test("external state change before setter", () => { var s = new FakeSurface(); s.OnRead = f => { if(f.Reads==2) f.State.global_lock_preroutes = true; }; var r = Apply(s,"{\"name\":\"Other\"}"); Require(!Flag(r,"success") && !Flag(r,"mutation_attempted") && s.Writes.Count==0); });
        test("hidden state change stops remaining setters", () => { var s = new FakeSurface(); s.OnWrite = (f,n) => f.State.active_strategy.passes = "unexpected"; var r = Apply(s,"{\"name\":\"Other\",\"description\":\"next\"}"); Require(!Flag(r,"success") && Flag(r,"outcome_unknown") && s.Writes.SequenceEqual(new[]{"name"})); Require(!Flag(r,"rollback_attempted") && !Flag(r,"retry_allowed")); });
        test("throw after effect yields fresh readback no replay", () => { var s = new FakeSurface(); s.OnWrite = (f,n) => { throw new Exception("simulated native failure"); }; var r = Apply(s,"{\"name\":\"Other\"}"); Require(!Flag(r,"success") && Flag(r,"mutation_attempted") && Flag(r,"outcome_unknown")); Require(r.GetProperty("after").GetProperty("active_strategy").GetProperty("name").GetString()=="Other" && s.Writes.Count==1); });
        test("failed read after setter remains unknown", () => { var s = new FakeSurface(); s.OnRead = f => { if(f.Reads>=3) throw new Exception("read unavailable"); }; var r = Apply(s,"{\"name\":\"Other\"}"); Require(!Flag(r,"success") && Flag(r,"outcome_unknown") && r.GetProperty("after").ValueKind==JsonValueKind.Null && r.GetProperty("changed").ValueKind==JsonValueKind.Null && s.Writes.Count==1); });
        test("second setter failure preserves honest partial outcome", () => { var s = new FakeSurface(); s.OnWrite = (f,n) => { if(n=="description") throw new Exception("second setter failure"); }; var r = Apply(s,"{\"name\":\"Other\",\"description\":\"changed\",\"prefer_orthogonal\":true}"); Require(!Flag(r,"success") && Flag(r,"outcome_unknown") && s.Writes.SequenceEqual(new[]{"name","description"}) && !s.State.active_strategy.prefer_orthogonal); });
        test("final no-op verification detects external change", () => { var s = new FakeSurface(); s.OnRead = f => { if(f.Reads==2) f.State.active_strategy.via_bias++; }; var r=Apply(s,"{\"name\":\"Current\"}"); Require(!Flag(r,"success") && !Flag(r,"mutation_attempted") && s.Writes.Count==0); });
        test("snapshot hash covers every protected field", () => { var initial=Initial(); string hash=SitusSettings.Hash(initial); Action<SettingsSnapshot>[] mutate={s=>s.pcb_path+="x",s=>s.board_identity+="x",s=>s.strategy_valid=false,s=>s.active_strategy.name+="x",s=>s.active_strategy.description+="x",s=>s.active_strategy.passes+="x",s=>s.active_strategy.via_bias++,s=>s.active_strategy.prefer_orthogonal=true,s=>s.global_lock_preroutes=true,s=>s.layer_directions[0].layer_id++,s=>s.layer_directions[0].layer_name+="x",s=>s.layer_directions[0].layer_used=false,s=>s.layer_directions[0].direction++,s=>s.layer_directions[0].direction_name+="x"}; foreach(var change in mutate){var state=SitusSettings.Copy(initial);change(state);Require(SitusSettings.Hash(state)!=hash);} });
        test("unqualified passes rejected",()=>Reject("{\"passes\":\"anything\"}"));
        test("unqualified via bias rejected",()=>Reject("{\"via_bias\":0}"));
        test("global lock write rejected",()=>Reject("{\"global_lock_preroutes\":true}"));
        test("arbitrary file paths rejected",()=>Reject("{\"import_path\":\"C:\\\\x\"}"));
        test("unknown direction rejected",()=>Reject("{\"layer_directions\":[{\"layer_id\":1001,\"direction\":12}]}"));
        test("negative direction rejected",()=>Reject("{\"layer_directions\":[{\"layer_id\":1001,\"direction\":-1}]}"));
        test("fractional direction rejected",()=>Reject("{\"layer_directions\":[{\"layer_id\":1001,\"direction\":1.5}]}"));
        test("duplicate layer rejected",()=>Reject("{\"layer_directions\":[{\"layer_id\":1001,\"direction\":1},{\"layer_id\":1001,\"direction\":2}]}"));
        test("duplicate setting rejected",()=>Reject("{\"name\":\"A\",\"name\":\"B\"}"));
        test("numeric boolean rejected",()=>Reject("{\"prefer_orthogonal\":1}"));
        test("empty change rejected",()=>Reject("{}"));
        test("empty directions rejected",()=>Reject("{\"layer_directions\":[]}"));
        test("blank name rejected",()=>Reject("{\"name\":\"  \"}"));
        test("null description rejected",()=>Reject("{\"description\":null}"));
        test("NUL text rejected",()=>Reject("{\"name\":\"bad\\u0000text\"}"));
        test("lowercase exact hash required",()=> {var s=new FakeSurface();bool failed=false;try{SitusSettings.ParseChange(Parse(Payload(s,"{\"name\":\"A\"}").Replace(SitusSettings.Hash(s.State),new string('A',64))));}catch(ArgumentException){failed=true;}Require(failed);});
        test("unknown payload field rejected",()=> {var s=new FakeSurface();bool failed=false;try{SitusSettings.ParseChange(Parse(Payload(s,"{\"name\":\"A\"}").Replace("\"changes\":","\"other\":true,\"changes\":")));}catch(ArgumentException){failed=true;}Require(failed);});
        Console.WriteLine("PASS total="+count+" native_calls=0"); return 0;
    }
}
internal static class SettingsTestProgram
{
    public static int Main(string[] args)
    {
        // Resolve metadata dependencies only; no SDK/native type is instantiated.
        string system=Path.GetFullPath(args[0]);
        AssemblyLoadContext.Default.Resolving += (context,name) => {
            if(!new[]{"Altium.SDK","Altium.SDK.Interfaces","Altium.Edp.Interfaces","Altium.Dxp.Interfaces"}.Contains(name.Name)) return null;
            return context.LoadFromAssemblyPath(Path.Combine(system,name.Name+".dll"));
        };
        return TestCases.Run();
    }
}
