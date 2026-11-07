// Daedalus play bridge for Unity players. Injected into a COPY of the project by the play worker; never edit by hand.
// Same JSON-lines protocol as the Godot bridge, on 127.0.0.1. Real keyboard/mouse input is sent by the OS (the player
// reads it through the normal input system); the legacy input system cannot be injected from inside, so send_key and
// send_mouse answer unsupported_input_route instead of pretending.
using System;
using System.Collections;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Net;
using System.Net.Sockets;
using System.Reflection;
using System.Text;
using System.Threading;
using UnityEngine;
using UnityEngine.SceneManagement;

namespace Daedalus
{
    public class PlayBridge : MonoBehaviour
    {
        const int MaxNodes = 240;
        TcpListener _listener;
        Thread _accept;
        volatile bool _stop;
        readonly ConcurrentQueue<Action> _inbox = new ConcurrentQueue<Action>();
        readonly object _writeLock = new object();
        NetworkStream _stream;
        readonly List<string> _errors = new List<string>();
        string _outDir = "";
        float _fps = 0f;
        int _waitFrames = -1;
        Dictionary<string, string> _waitReq;

        [RuntimeInitializeOnLoadMethod(RuntimeInitializeLoadType.BeforeSceneLoad)]
        static void Boot()
        {
            int port = 0;
            string[] args = Environment.GetCommandLineArgs();
            string outDir = "";
            for (int i = 0; i < args.Length - 1; i++)
            {
                if (args[i] == "--daedalus-bridge") int.TryParse(args[i + 1], out port);
                if (args[i] == "--daedalus-out") outDir = args[i + 1];
            }
            if (port <= 0) return;                       // not launched by the play worker: do nothing
            var go = new GameObject("DaedalusPlayBridge");
            DontDestroyOnLoad(go);
            go.hideFlags = HideFlags.HideAndDontSave;
            var b = go.AddComponent<PlayBridge>();
            b._outDir = outDir;
            b.Listen(port);
        }

        void Listen(int port)
        {
            Application.runInBackground = true;
            Application.logMessageReceived += OnLog;
            _listener = new TcpListener(IPAddress.Loopback, port);
            _listener.Start();
            _accept = new Thread(AcceptLoop) { IsBackground = true };
            _accept.Start();
        }

        void OnLog(string msg, string stack, LogType type)
        {
            if (type == LogType.Error || type == LogType.Exception || type == LogType.Assert)
            {
                lock (_errors) { _errors.Add(msg.Length > 300 ? msg.Substring(0, 300) : msg); if (_errors.Count > 40) _errors.RemoveAt(0); }
            }
        }

        void AcceptLoop()
        {
            while (!_stop)
            {
                try
                {
                    var client = _listener.AcceptTcpClient();
                    lock (_writeLock) { _stream = client.GetStream(); }
                    ReadLoop(client);
                }
                catch (Exception) { if (_stop) return; Thread.Sleep(50); }
            }
        }

        void ReadLoop(TcpClient client)
        {
            var buf = new byte[8192];
            var sb = new StringBuilder();
            var stream = client.GetStream();
            while (!_stop && client.Connected)
            {
                int n;
                try { n = stream.Read(buf, 0, buf.Length); } catch (Exception) { return; }
                if (n <= 0) return;
                sb.Append(Encoding.UTF8.GetString(buf, 0, n));
                int nl;
                while ((nl = sb.ToString().IndexOf('\n')) >= 0)
                {
                    string line = sb.ToString(0, nl).Trim();
                    sb.Remove(0, nl + 1);
                    if (line.Length == 0) continue;
                    string captured = line;
                    _inbox.Enqueue(() => Handle(captured));
                }
            }
        }

        void Update()
        {
            float dt = Time.unscaledDeltaTime;
            if (dt > 0f) _fps = Mathf.Lerp(_fps, 1f / dt, 0.1f);
            Action a;
            int guard = 0;
            while (guard++ < 8 && _inbox.TryDequeue(out a)) a();
            if (_waitFrames > 0 && --_waitFrames == 0 && _waitReq != null)
            {
                var req = _waitReq; _waitReq = null; _waitFrames = -1;
                Reply(req, "\"waited\":" + req["n"] + ",\"fps\":" + F(_fps));
            }
        }

        void OnDestroy() { _stop = true; try { _listener?.Stop(); } catch (Exception) { } }

        // ---- protocol ---------------------------------------------------------------------------------------------
        void Handle(string line)
        {
            Dictionary<string, string> req;
            try { req = ParseFlat(line); }
            catch (Exception) { Write("{\"ok\":false,\"error\":\"not_json_object\"}"); return; }
            string op = Get(req, "op");
            switch (op)
            {
                case "ping":
                    Reply(req, "\"pong\":true,\"fps\":" + F(_fps) + ",\"game\":{}"); break;
                case "fps_sample":
                    Reply(req, "\"fps\":" + F(_fps) + ",\"headless\":" + (SystemInfo.graphicsDeviceType == UnityEngine.Rendering.GraphicsDeviceType.Null ? "true" : "false")); break;
                case "scene_tree":
                    Reply(req, "\"tree\":" + SceneTree()); break;
                case "errors":
                    string errs; lock (_errors) { errs = "[" + string.Join(",", _errors.ConvertAll(e => Q(e)).ToArray()) + "]"; }
                    Reply(req, "\"errors\":" + errs + ",\"notes\":[],\"game\":{}"); break;
                case "wait_frames":
                    int n = Mathf.Clamp(ParseInt(Get(req, "n"), 1), 1, 180);
                    req["n"] = n.ToString(CultureInfo.InvariantCulture);
                    _waitReq = req; _waitFrames = n; break;
                case "screenshot":
                    StartCoroutine(Screenshot(req)); break;
                case "call_method":
                    CallMethod(req); break;
                case "send_key":
                case "send_mouse":
                case "send_input":
                    Fail(req, "unsupported_input_route"); break;
                case "quit":
                    Reply(req, ""); Application.Quit(); break;
                default:
                    Fail(req, "unknown_op"); break;
            }
        }

        IEnumerator Screenshot(Dictionary<string, string> req)
        {
            yield return new WaitForEndOfFrame();
            Texture2D tex = null;
            try
            {
                tex = ScreenCapture.CaptureScreenshotAsTexture();
                if (tex == null || tex.width < 2) { Fail(req, "empty_image"); yield break; }
                string dir = string.IsNullOrEmpty(_outDir) ? Application.persistentDataPath : _outDir;
                Directory.CreateDirectory(dir);
                string path = Path.Combine(dir, "bridge_shot.png");
                File.WriteAllBytes(path, tex.EncodeToPNG());
                Reply(req, "\"path\":" + Q("bridge_shot.png") + ",\"w\":" + tex.width + ",\"h\":" + tex.height);
            }
            finally { if (tex != null) Destroy(tex); }
        }

        void CallMethod(Dictionary<string, string> req)
        {
            string path = Get(req, "path"), method = Get(req, "method");
            string name = path.Substring(path.LastIndexOf('/') + 1);
            var go = GameObject.Find(name);
            if (go == null) { Fail(req, "node_not_found"); return; }
            foreach (var comp in go.GetComponents<MonoBehaviour>())
            {
                if (comp == null) continue;
                var m = comp.GetType().GetMethod(method, BindingFlags.Public | BindingFlags.Instance, null, Type.EmptyTypes, null);
                if (m == null) continue;
                object ret = m.Invoke(comp, null);
                string s = ret as string;
                if (s != null && s.TrimStart().StartsWith("{")) Reply(req, "\"result_json\":" + Q(s));
                else Reply(req, "\"result_json\":" + Q(ret == null ? "null" : Q(ret.ToString())));
                return;
            }
            Fail(req, "missing_method");
        }

        string SceneTree()
        {
            var parts = new List<string>();
            var scene = SceneManager.GetActiveScene();
            foreach (var root in scene.GetRootGameObjects())
                Walk(root.transform, "/" + root.name, parts);
            return "[" + string.Join(",", parts.ToArray()) + "]";
        }

        void Walk(Transform t, string path, List<string> parts)
        {
            if (parts.Count >= MaxNodes) return;
            parts.Add("{\"name\":" + Q(t.name) + ",\"type\":" + Q(t.GetType().Name) + ",\"path\":" + Q(path) + "}");
            for (int i = 0; i < t.childCount && parts.Count < MaxNodes; i++)
                Walk(t.GetChild(i), path + "/" + t.GetChild(i).name, parts);
        }

        // ---- tiny JSON helpers (flat requests only) --------------------------------------------------------------
        void Reply(Dictionary<string, string> req, string fields)
        {
            string id = req.ContainsKey("id") ? req["id"] : "0";
            Write("{\"id\":" + id + ",\"ok\":true,\"op\":" + Q(Get(req, "op")) + (fields.Length > 0 ? "," + fields : "") + "}");
        }

        void Fail(Dictionary<string, string> req, string error)
        {
            string id = req.ContainsKey("id") ? req["id"] : "0";
            Write("{\"id\":" + id + ",\"ok\":false,\"op\":" + Q(Get(req, "op")) + ",\"error\":" + Q(error) + "}");
        }

        void Write(string json)
        {
            lock (_writeLock)
            {
                if (_stream == null) return;
                try { var b = Encoding.UTF8.GetBytes(json + "\n"); _stream.Write(b, 0, b.Length); _stream.Flush(); } catch (Exception) { }
            }
        }

        static string Get(Dictionary<string, string> d, string k) { string v; return d.TryGetValue(k, out v) ? v : ""; }
        static int ParseInt(string s, int dflt) { int v; return int.TryParse(s, NumberStyles.Integer, CultureInfo.InvariantCulture, out v) ? v : dflt; }
        static string F(float f) { return f.ToString("0.##", CultureInfo.InvariantCulture); }

        static string Q(string s)
        {
            var sb = new StringBuilder("\"");
            foreach (char c in s ?? "")
            {
                if (c == '"') sb.Append("\\\""); else if (c == '\\') sb.Append("\\\\");
                else if (c == '\n') sb.Append("\\n"); else if (c == '\r') sb.Append("\\r"); else if (c == '\t') sb.Append("\\t");
                else if (c < 0x20) sb.Append("\\u").Append(((int)c).ToString("x4"));
                else sb.Append(c);
            }
            return sb.Append('"').ToString();
        }

        // Parses {"k":"string"|number|true|false|null, ...}; nested values (arrays/objects) are skipped.
        static Dictionary<string, string> ParseFlat(string json)
        {
            var d = new Dictionary<string, string>();
            int i = json.IndexOf('{');
            if (i < 0) throw new FormatException();
            i++;
            while (i < json.Length)
            {
                while (i < json.Length && (char.IsWhiteSpace(json[i]) || json[i] == ',')) i++;
                if (i >= json.Length || json[i] == '}') break;
                string key = ReadString(json, ref i);
                while (i < json.Length && (char.IsWhiteSpace(json[i]) || json[i] == ':')) i++;
                string val;
                if (json[i] == '"') val = ReadString(json, ref i);
                else if (json[i] == '[' || json[i] == '{') { SkipNested(json, ref i); val = ""; }
                else { int s = i; while (i < json.Length && json[i] != ',' && json[i] != '}') i++; val = json.Substring(s, i - s).Trim(); }
                d[key] = val;
            }
            return d;
        }

        static string ReadString(string s, ref int i)
        {
            if (s[i] != '"') throw new FormatException();
            i++;
            var sb = new StringBuilder();
            while (i < s.Length && s[i] != '"')
            {
                if (s[i] == '\\' && i + 1 < s.Length)
                {
                    i++;
                    char c = s[i];
                    sb.Append(c == 'n' ? '\n' : c == 't' ? '\t' : c == 'r' ? '\r' : c);
                }
                else sb.Append(s[i]);
                i++;
            }
            i++;
            return sb.ToString();
        }

        static void SkipNested(string s, ref int i)
        {
            int depth = 0; bool str = false;
            for (; i < s.Length; i++)
            {
                char c = s[i];
                if (str) { if (c == '\\') i++; else if (c == '"') str = false; continue; }
                if (c == '"') str = true;
                else if (c == '[' || c == '{') depth++;
                else if (c == ']' || c == '}') { depth--; if (depth == 0) { i++; return; } }
            }
        }
    }
}
