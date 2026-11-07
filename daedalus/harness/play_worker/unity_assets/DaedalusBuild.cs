// Editor-only: builds a Windows player for the play worker (batchmode). Injected into a COPY of the project.
#if UNITY_EDITOR
using System;
using System.IO;
using System.Linq;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;

namespace Daedalus
{
    public static class PlayBuild
    {
        public static void BuildWin64()
        {
            string[] args = Environment.GetCommandLineArgs();
            string output = "";
            for (int i = 0; i < args.Length - 1; i++) if (args[i] == "-daedalusBuildOutput") output = args[i + 1];
            if (string.IsNullOrEmpty(output)) { Debug.LogError("DAEDALUS_BUILD: no -daedalusBuildOutput"); EditorApplication.Exit(2); return; }

            // The window must keep running (and answering the bridge) when the owner clicks elsewhere.
            PlayerSettings.runInBackground = true;
            PlayerSettings.fullScreenMode = FullScreenMode.Windowed;
            PlayerSettings.defaultScreenWidth = 640;
            PlayerSettings.defaultScreenHeight = 480;
            PlayerSettings.resizableWindow = true;
            PlayerSettings.visibleInBackground = true;
            PlayerSettings.SplashScreen.show = false;

            var scenes = EditorBuildSettings.scenes.Where(s => s.enabled).Select(s => s.path).ToList();
            if (scenes.Count == 0)
            {
                // Games without a scene in the build list (for example a scene-less fixture): an empty startup scene.
                Directory.CreateDirectory("Assets/DaedalusAuto");
                var scene = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);
                var cam = new GameObject("Main Camera");
                cam.AddComponent<Camera>().tag = "MainCamera";
                EditorSceneManager.SaveScene(scene, "Assets/DaedalusAuto/Startup.unity");
                scenes.Add("Assets/DaedalusAuto/Startup.unity");
            }
            Directory.CreateDirectory(output);
            var opts = new BuildPlayerOptions
            {
                scenes = scenes.ToArray(),
                locationPathName = Path.Combine(output, "Game.exe"),
                target = BuildTarget.StandaloneWindows64,
                options = BuildOptions.Development,
            };
            var report = BuildPipeline.BuildPlayer(opts);
            Debug.Log("DAEDALUS_BUILD: " + report.summary.result + " errors=" + report.summary.totalErrors);
            EditorApplication.Exit(report.summary.result == UnityEditor.Build.Reporting.BuildResult.Succeeded ? 0 : 1);
        }
    }
}
#endif
