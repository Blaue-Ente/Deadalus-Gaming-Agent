// Compiled at install time to Daedalus.exe (no extra SDK).
// Starts the venv interpreter with ``-m app.cli desktop`` and waits so
// Task Manager shows Daedalus.exe until Studio closes.
using System;
using System.Diagnostics;
using System.IO;
using System.Windows.Forms;

internal static class Program
{
    [STAThread]
    private static int Main()
    {
        string root = AppDomain.CurrentDomain.BaseDirectory;
        string pyw = Path.Combine(root, ".venv", "Scripts", "pythonw.exe");
        string py = Path.Combine(root, ".venv", "Scripts", "python.exe");
        string exe = File.Exists(pyw) ? pyw : py;
        if (!File.Exists(exe))
        {
            MessageBox.Show(
                "Daedalus is not installed correctly." + Environment.NewLine +
                "Missing: " + exe + Environment.NewLine +
                "Run packaging\\windows\\install.ps1 again.",
                "Daedalus",
                MessageBoxButtons.OK,
                MessageBoxIcon.Error);
            return 1;
        }

        ProcessStartInfo psi = new ProcessStartInfo();
        psi.FileName = exe;
        psi.Arguments = "-m app.cli desktop";
        psi.WorkingDirectory = root;
        psi.UseShellExecute = false;
        psi.CreateNoWindow = true;

        string local = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
        string data = Path.Combine(local, "Daedalus");
        psi.EnvironmentVariables["DAEDALUS_STANDALONE"] = "1";
        psi.EnvironmentVariables["DAEDALUS_WINDOWS_APP"] = "1";
        psi.EnvironmentVariables["DAEDALUS_DATA"] = data;
        psi.EnvironmentVariables["DAEDALUS_PLAYGROUND"] = Path.Combine(data, "playground");
        psi.EnvironmentVariables["HOST"] = "127.0.0.1";
        psi.EnvironmentVariables["PORT"] = "8787";
        psi.EnvironmentVariables["PYTHONUTF8"] = "1";
        psi.EnvironmentVariables["PYTHONIOENCODING"] = "utf-8";

        try
        {
            Process proc = Process.Start(psi);
            if (proc == null)
            {
                return 1;
            }
            proc.WaitForExit();
            return proc.ExitCode;
        }
        catch (Exception ex)
        {
            MessageBox.Show(ex.Message, "Daedalus", MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 1;
        }
    }
}
