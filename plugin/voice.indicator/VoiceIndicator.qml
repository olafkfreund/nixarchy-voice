import QtQuick
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Ui

// Reads the state file the daemon writes on every transition, so the bar
// reflects what the assistant is doing without polling a process.
BarWidget {
  id: root
  moduleName: "voice.indicator"

  property string status: "stopped"
  property string label: ""

  // Mirrors VoiceOrb's tint so the bar and the orb never disagree about how
  // serious a state is. Painted from the live Omarchy palette — no colours of
  // our own, so a theme switch re-tints it on the next repaint.
  readonly property color tint: {
    if (status === "confirm" || status === "error")
      return root.bar ? root.bar.urgent : Color.urgent
    if (status === "listening" || status === "thinking" || status === "acting")
      return Color.accent
    return root.bar ? root.bar.barForeground : Color.foreground
  }

  // How much of the mark is radiating. Shape carries the state as well as
  // colour does, so a warning is still legible to someone who cannot see red.
  readonly property int rings: {
    if (status === "listening" || status === "thinking"
        || status === "acting" || status === "confirm") return 2
    if (status === "idle") return 1
    return 0
  }

  // Nothing is running and nothing is wrong. Sit down.
  readonly property bool asleep: status === "stopped" || status === "unconfigured"

  FileView {
    id: state
    path: Quickshell.env("XDG_RUNTIME_DIR") + "/omarchy-voice/state.json"
    watchChanges: true
    onFileChanged: reload()
    onLoaded: {
      try {
        const parsed = JSON.parse(state.text())
        root.status = parsed.status || "idle"
        root.label = parsed.text || ""
      } catch (e) {
        root.status = "error"
        root.label = ""
      }
    }
    onLoadFailed: {
      root.status = "stopped"
      root.label = ""
    }
  }

  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  // Only "confirm" breathes. It is the one state that is *waiting on you*, and
  // a still mark in a busy bar is easy to walk past. Everything else holds
  // steady — a panel that moves for routine work becomes background noise.
  SequentialAnimation on opacity {
    running: root.status === "confirm"
    loops: Animation.Infinite
    alwaysRunToEnd: true
    NumberAnimation { from: 1.0; to: 0.45; duration: 620; easing.type: Easing.InOutSine }
    NumberAnimation { from: 0.45; to: 1.0; duration: 620; easing.type: Easing.InOutSine }
  }

  BarIconButton {
    id: button
    anchors.fill: parent
    bar: root.bar

    // A drawn mark instead of `text`, because every stock voice glyph in the
    // font is already spoken for — see the note at the top of VoiceMark.qml.
    iconComponent: Component {
      Item {
        VoiceMark {
          anchors.centerIn: parent
          // A hair under the optical canvas: the outer halo's round caps reach
          // the edge of the grid, where a font glyph would leave side bearing.
          iconSize: Style.bar.iconCanvas * 0.92
          color: root.tint
          rings: root.rings
          core: root.status === "acting" ? 22 : 15
          alert: root.status === "confirm"
          crossed: root.status === "error"
          spinning: root.status === "thinking"
          // Matches WidgetButton's own dimming for an inactive widget.
          opacity: root.asleep ? 0.45 : 1.0
          Behavior on opacity { NumberAnimation { duration: 160 } }
        }
      }
    }

    tooltipText: root.label !== ""
                 ? root.status + " — " + root.label
                 : "Voice control: " + root.status
    onPressed: function(b) {
      if (root.status === "confirm")
        root.bar.run("omarchy-voice listen confirm")
      else
        root.bar.run("omarchy-voice listen toggle")
    }
  }
}
