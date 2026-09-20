import QtQuick
import QtQuick.Shapes
import qs.Commons

// Our own mark rather than a font glyph.
//
// The bar already carries a microphone four times over — the shell's own
// Microphone and Dictation widgets, voxtype-osd and omarecorder all draw
// U+F036C — and a robot (skal.bar, U+F06A9). Any stock "voice" glyph therefore
// reads as one of those, which is exactly the confusion we are trying to
// avoid. A drawn mark cannot collide with a plugin we have never heard of.
//
// The shape is the VoiceOrb overlay in miniature: a core with halo arcs
// radiating from it. Seeing this in the bar and seeing the orb at the bottom
// of the screen should feel like the same object in two places.
//
// Geometry is written on a 100x100 grid and scaled by `u`, so the mark keeps
// its proportions at whatever Style.bar.iconFont the theme picks.
Item {
  id: root

  property real iconSize: 16
  property color color: Color.foreground
  // How much is radiating: 0 nothing, 1 inner halo, 2 both.
  property int rings: 0
  // Swap the core for an exclamation — something to read, not to admire.
  property bool alert: false
  // Strike it through: the mark is present but the thing behind it is not.
  property bool crossed: false
  // Thinking. The halo turns instead of the core, so the centre stays still.
  property bool spinning: false
  // Core radius on the 100 grid. "acting" swells it, so doing something does
  // not look identical to merely hearing something.
  property real core: 15

  readonly property real u: iconSize / 100

  implicitWidth: iconSize
  implicitHeight: iconSize
  width: iconSize
  height: iconSize

  // ---- halo -------------------------------------------------------------
  // Separate from the core so it can rotate on its own. Arcs rather than full
  // rings: less ink, which is what keeps this legible at 13px.
  Item {
    id: halo
    anchors.fill: parent

    RotationAnimator on rotation {
      running: root.spinning
      loops: Animation.Infinite
      from: 0; to: 360; duration: 1600
    }
    // A stopped animator leaves the last angle behind; park it square again.
    onRotationChanged: if (!root.spinning && rotation !== 0) rotation = 0

    Shape {
      anchors.fill: parent
      antialiasing: true
      layer.enabled: true
      layer.samples: 4

      Halo { radius: 30; facing: 0;   shown: root.rings >= 1 }
      Halo { radius: 30; facing: 180; shown: root.rings >= 1 }
      Halo { radius: 45; facing: 0;   shown: root.rings >= 2 }
      Halo { radius: 45; facing: 180; shown: root.rings >= 2 }
    }
  }

  // ---- core -------------------------------------------------------------
  Shape {
    anchors.fill: parent
    antialiasing: true
    layer.enabled: true
    layer.samples: 4

    // The dot at the centre. Hidden when an exclamation takes its place.
    ShapePath {
      fillColor: root.alert ? "transparent" : root.color
      strokeWidth: 0
      PathAngleArc {
        centerX: 50 * root.u; centerY: 50 * root.u
        radiusX: root.core * root.u; radiusY: root.core * root.u
        startAngle: 0; sweepAngle: 360
      }
    }

    // Exclamation stem.
    ShapePath {
      fillColor: root.alert ? root.color : "transparent"
      strokeWidth: 0
      startX: 43 * root.u; startY: 26 * root.u
      PathLine { x: 57 * root.u; y: 26 * root.u }
      PathLine { x: 57 * root.u; y: 57 * root.u }
      PathLine { x: 43 * root.u; y: 57 * root.u }
      PathLine { x: 43 * root.u; y: 26 * root.u }
    }

    // Exclamation dot.
    ShapePath {
      fillColor: root.alert ? root.color : "transparent"
      strokeWidth: 0
      PathAngleArc {
        centerX: 50 * root.u; centerY: 70 * root.u
        radiusX: 8 * root.u; radiusY: 8 * root.u
        startAngle: 0; sweepAngle: 360
      }
    }

    // Strike-through.
    ShapePath {
      fillColor: "transparent"
      strokeColor: root.crossed ? root.color : "transparent"
      strokeWidth: 11 * root.u
      capStyle: ShapePath.RoundCap
      startX: 22 * root.u; startY: 22 * root.u
      PathLine { x: 78 * root.u; y: 78 * root.u }
    }
  }

  // A halo arc. `facing` picks the side; two of them read as radiating, where
  // one alone would read as a bracket.
  component Halo: ShapePath {
    property real radius: 0
    property real facing: 0
    property bool shown: false

    fillColor: "transparent"
    strokeColor: shown ? root.color : "transparent"
    strokeWidth: 10 * root.u
    capStyle: ShapePath.RoundCap
    PathAngleArc {
      centerX: 50 * root.u; centerY: 50 * root.u
      radiusX: radius * root.u; radiusY: radius * root.u
      startAngle: facing - 36; sweepAngle: 72
      moveToStart: true
    }
  }
}
