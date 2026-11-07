extends Control
## Fixture for the Daedalus play worker. The colour and label change ONLY on real input,
## so a frame diff proves the input arrived. A hidden label ("secret") is for the mentor check.

var presses: int = 0
var clicks: int = 0

@onready var panel: ColorRect = $Panel
@onready var status: Label = $Status
@onready var secret: Label = $Secret

func _ready() -> void:
	# A label the harness picks per run (env), so a model can only "read" it if it really gets the image.
	secret.text = OS.get_environment("DAEDALUS_FIXTURE_LABEL")

func _input(event: InputEvent) -> void:
	if event is InputEventKey and event.pressed and not event.echo:
		if event.keycode == KEY_SPACE:
			presses += 1
			panel.color = Color(0.1, 0.7, 0.2)
			status.text = "space x%d" % presses
	elif event is InputEventMouseButton and event.pressed:
		clicks += 1
		panel.color = Color(0.1, 0.2, 0.85)
		status.text = "click x%d at %d,%d" % [clicks, int(event.position.x), int(event.position.y)]

func state() -> Dictionary:
	return {"presses": presses, "clicks": clicks, "label": status.text}
