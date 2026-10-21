// Separate candidate extension. No routing command is exposed in this build.
using System;
using System.Runtime.InteropServices;
using DXP;

namespace CSharpPlugin
{
    [ComVisible(true)]
    [ClassInterface(ClassInterfaceType.None)]
    public sealed class PluginFactory
    {
        public object InvokePluginFactory(IClient client)
        {
            if (client == null) throw new ArgumentNullException("client");
            return new NativeSitusControl.Server(client);
        }
    }
}
namespace NativeSitusControl
{
    public sealed class Server : ServerModule
    {
        public Server(IClient client) : base(client, "NativeSitusControl") { }
        protected override IServerDocument NewDocumentInstance(string kind, string fileName) { return null; }
        protected override void InitializeCommands()
        {
            ((CommandLauncher)CommandLauncher).RegisterCommand("ExecuteRequest", ExecuteRequest, GetState);
        }
        private static void GetState(IServerDocumentView context, ref string parameters,
            ref bool enabled, ref bool isChecked, ref bool visible,
            ref string caption, ref string imageFile)
        {
            enabled = context != null; visible = true; isChecked = false;
        }
        public static void ExecuteRequest(IServerDocumentView context, ref string parameters)
        {
            NativeSitusNext.NativeTransport.Execute(context, ref parameters,
                NativeSitusNext.SitusSettings.Dispatch);
        }
    }
}
