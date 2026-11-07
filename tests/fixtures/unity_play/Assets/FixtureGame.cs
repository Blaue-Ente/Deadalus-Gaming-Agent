// Test fixture for the Daedalus play worker. The colour and label change ONLY on real input (legacy input system),
// so a frame difference proves the input arrived. DAEDALUS_FIXTURE_LABEL is drawn when set (hidden-label check).
using UnityEngine;

public class FixtureGame : MonoBehaviour
{
    int presses, clicks;
    string label = "idle";
    Color panel = new Color(0.8f, 0.1f, 0.1f);
    string secret = "";
    Vector2 lastClick;

    [RuntimeInitializeOnLoadMethod(RuntimeInitializeLoadType.AfterSceneLoad)]
    static void Boot()
    {
        var go = new GameObject("FixtureGame");
        go.AddComponent<FixtureGame>();
    }

    void Awake()
    {
        secret = System.Environment.GetEnvironmentVariable("DAEDALUS_FIXTURE_LABEL") ?? "";
    }

    void Update()
    {
        if (Input.GetKeyDown(KeyCode.Space))
        {
            presses++;
            panel = new Color(0.1f, 0.7f, 0.2f);
            label = "space x" + presses;
        }
        if (Input.GetMouseButtonDown(0))
        {
            clicks++;
            lastClick = Input.mousePosition;
            panel = new Color(0.1f, 0.2f, 0.85f);
            label = "click x" + clicks + " at " + (int)lastClick.x + "," + (int)(Screen.height - lastClick.y);
        }
    }

    void OnGUI()
    {
        var prev = GUI.color;
        GUI.color = panel;
        GUI.DrawTexture(new Rect(0, 0, Screen.width, Screen.height), Texture2D.whiteTexture);
        GUI.color = new Color(0.95f, 0.85f, 0.2f);
        GUI.DrawTexture(new Rect(0, Screen.height - 40, Screen.width, 20), Texture2D.whiteTexture);
        GUI.color = new Color(0.2f, 0.8f, 0.9f);
        GUI.DrawTexture(new Rect(0, Screen.height - 20, Screen.width, 20), Texture2D.whiteTexture);
        GUI.color = Color.white;
        var big = new GUIStyle(GUI.skin.label) { fontSize = 22, fontStyle = FontStyle.Bold };
        GUI.Label(new Rect(8, 8, Screen.width - 16, 36), label, big);
        if (secret.Length > 0) GUI.Label(new Rect(8, 90, Screen.width - 16, 44), secret, big);
        GUI.color = prev;
    }

    // Read by the bridge (call_method): the game's own view of what happened.
    public string state()
    {
        return "{\"presses\":" + presses + ",\"clicks\":" + clicks + ",\"label\":\"" + label + "\"}";
    }
}
