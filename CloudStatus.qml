import QtQuick
import Quickshell.Io
import qs.Ui
import qs.Commons
import qs.Commons as Commons

BarWidget {
  id: root
  moduleName: "cjvnjde.cloud-sync-status"
  property var snapshot: ({clouds: [], error: "", updated: 0})
  property bool popupOpen: false
  readonly property bool opened: popupOpen
  property real tick: Date.now()
  readonly property var clouds: snapshot.clouds || []
  readonly property bool hasError: !!snapshot.error || clouds.some(c => c.state === "error" || c.state === "unavailable")
  readonly property int syncingCount: clouds.filter(c => c.state === "syncing").length
  readonly property string glyph: hasError ? "󰅚" : syncingCount > 0 ? "󰅠" : "󰅟"
  readonly property color muted: Qt.alpha(bar ? bar.foreground : "white", 0.58)
  readonly property color iconColor: hasError ? bar.urgent : syncingCount > 0 ? Commons.Color.accent : clouds.length > 0 && clouds.every(c => c.state === "synced") ? "#a6da95" : muted
  implicitWidth: barSize
  implicitHeight: barSize

  function open() { popupOpen = true }
  function close() { popupOpen = false }
  function toggle() { popupOpen = !popupOpen }
  function refresh() { if (!collector.running) collector.running = true }
  function statusLabel(c) {
    switch (c.state) {
      case "syncing": return c.phase === "scanning" ? "Scanning / checking folders" : c.initializing ? "Initial sync" : "Syncing"
      case "synced": return "Up to date"
      case "error": return "Sync failed"
      case "mounted": return "Mounted · on demand"
      case "waiting": return "Waiting for sync"
      case "stopped": return "Stopped"
      case "unavailable": return "Status unavailable"
      default: return "No sync job detected"
    }
  }
  function stateColor(c) {
    if (c.state === "error" || c.state === "unavailable") return bar ? bar.urgent : "#ed8796"
    if (c.state === "syncing") return Commons.Color.accent
    if (c.state === "synced") return "#a6da95"
    return root.muted
  }
  function size(n) {
    if (n === null || n === undefined) return "—"
    var units = ["B", "KiB", "MiB", "GiB", "TiB"]
    var i = 0
    while (n >= 1024 && i < units.length - 1) { n /= 1024; i++ }
    return n.toFixed(i === 0 ? 0 : 1) + " " + units[i]
  }
  function age(timestamp) {
    if (!timestamp) return "No report yet"
    var seconds = Math.max(0, Math.floor(root.tick / 1000 - timestamp))
    if (seconds < 60) return seconds + "s ago"
    if (seconds < 3600) return Math.floor(seconds / 60) + "m ago"
    if (seconds < 86400) return Math.floor(seconds / 3600) + "h ago"
    return Math.floor(seconds / 86400) + "d ago"
  }
  function tooltip() {
    if (snapshot.error) return "Cloud Sync Status\n" + snapshot.error
    var summary = "Cloud Sync Status\n" + (clouds.length ? clouds.map(c => c.remote + ": " + statusLabel(c)).join("\n") : "No rclone clouds detected")
    return summary
  }

  Process {
    id: collector
    command: ["python3", decodeURIComponent(Qt.resolvedUrl("status.py").toString().replace(/^file:\/\//, "")), "--watch", "5"]
    running: true
    stdout: SplitParser {
      onRead: function(data) {
        try { root.snapshot = JSON.parse(data) }
        catch (error) { root.snapshot = {clouds: root.clouds, error: "Could not read sync status", updated: 0} }
      }
    }
    onExited: function(exitCode) {
      root.snapshot = {clouds: root.clouds, error: "Status monitor stopped", updated: 0}
      retry.restart()
    }
  }
  Timer { id: retry; interval: 10000; onTriggered: root.refresh() }
  Timer { interval: 5000; running: true; repeat: true; onTriggered: root.tick = Date.now() }

  Row {
    id: barRow
    anchors.centerIn: parent
    spacing: Style.space(5)
    Text {
      text: root.glyph
      color: root.iconColor
      font.family: root.bar.fontFamily
      font.pixelSize: Style.font.body
      anchors.verticalCenter: parent.verticalCenter
    }
  }
  MouseArea {
    anchors.fill: parent
    hoverEnabled: true
    cursorShape: Qt.PointingHandCursor
    onClicked: root.toggle()
    onEntered: if (root.bar) root.bar.showTooltip(root, root.tooltip())
    onExited: if (root.bar) root.bar.hideTooltip(root)
  }

  PopupCard {
    id: popup
    anchorItem: root
    bar: root.bar
    owner: root
    open: root.popupOpen
    contentWidth: fittedContentWidth(Style.space(420))
    contentHeight: fittedContentHeight(content.implicitHeight, Style.space(680))

    Flickable {
      anchors.fill: parent
      contentWidth: width
      contentHeight: content.implicitHeight
      clip: true
      boundsBehavior: Flickable.StopAtBounds

      Column {
        id: content
        width: parent.width
        spacing: Style.space(14)
        Column {
          width: parent.width
          spacing: Style.space(4)
          Text {
            text: "Cloud sync"
            color: root.bar.foreground
            font.family: root.bar.fontFamily
            font.pixelSize: Style.font.subtitle
            font.bold: true
          }
          Text {
            text: root.clouds.length + " cloud job" + (root.clouds.length === 1 ? "" : "s") + "  ·  " + (root.syncingCount ? root.syncingCount + " syncing" : "Status overview")
            color: root.muted
            font.family: root.bar.fontFamily
            font.pixelSize: Style.font.caption
          }
        }
        Text {
          visible: !!root.snapshot.error
          width: parent.width
          text: root.snapshot.error || ""
          textFormat: Text.PlainText
          wrapMode: Text.Wrap
          color: root.bar.urgent
          font.family: root.bar.fontFamily
          font.pixelSize: Style.font.bodySmall
        }
        Text {
          visible: !root.clouds.length && !root.snapshot.error
          width: parent.width
          text: "No rclone clouds detected. Sync jobs appear here automatically when they run."
          wrapMode: Text.Wrap
          color: root.muted
          font.family: root.bar.fontFamily
          font.pixelSize: Style.font.bodySmall
        }
        Repeater {
          model: root.clouds
          delegate: Rectangle {
            id: card
            required property var modelData
            readonly property var cloud: modelData
            width: content.width
            implicitHeight: details.implicitHeight + Style.space(24)
            radius: Style.space(7)
            color: Qt.alpha(root.bar.foreground, 0.035)
            border.color: Qt.alpha(root.bar.foreground, 0.14)

            Column {
              id: details
              anchors.left: parent.left
              anchors.right: parent.right
              anchors.top: parent.top
              anchors.margins: Style.space(12)
              spacing: Style.space(9)

              Row {
                width: parent.width
                spacing: Style.space(8)
                Rectangle {
                  width: Style.space(7); height: width; radius: width / 2
                  color: root.stateColor(card.cloud)
                  anchors.verticalCenter: parent.verticalCenter
                }
                Text {
                  width: parent.width - Style.space(15)
                  text: card.cloud.provider + "  ·  " + card.cloud.remote
                  textFormat: Text.PlainText
                  elide: Text.ElideRight
                  font.family: root.bar.fontFamily
                  font.pixelSize: Style.font.body
                  font.bold: true
                  color: root.bar.foreground
                }
              }
              Text {
                text: root.statusLabel(card.cloud)
                color: root.stateColor(card.cloud)
                font.family: root.bar.fontFamily
                font.pixelSize: Style.font.bodySmall
              }
              Text {
                visible: !!card.cloud.localPath
                width: parent.width
                text: card.cloud.localPath || ""
                textFormat: Text.PlainText
                wrapMode: Text.WrapAnywhere
                color: root.bar.foreground
                font.family: root.bar.fontFamily
                font.pixelSize: Style.font.caption
              }
              Text {
                width: parent.width
                text: (card.cloud.remotePath || "") + (card.cloud.mode ? "  ·  " + (card.cloud.mode === "bisync" ? "two-way sync" : card.cloud.mode) : "")
                textFormat: Text.PlainText
                wrapMode: Text.WrapAnywhere
                color: root.muted
                font.family: root.bar.fontFamily
                font.pixelSize: Style.font.caption
              }
              Grid {
                visible: (card.cloud.completedFiles || 0) > 0 || (card.cloud.queuedFiles || 0) > 0 || (card.cloud.bytes || 0) > 0 || (card.cloud.activeFiles || []).length > 0
                columns: 2
                columnSpacing: Style.space(12)
                rowSpacing: Style.space(5)
                width: parent.width
                Repeater {
                  model: [
                    ["Files transferred", (card.cloud.limitedHistory ? "≥ " : "") + (card.cloud.completedFiles || 0)],
                    ["Queued + active", card.cloud.queuedFiles === null || card.cloud.queuedFiles === undefined ? "Not reported" : card.cloud.queuedFiles],
                    ["Transferred", root.size(card.cloud.bytes)],
                    ["Queued data", root.size(card.cloud.queuedBytes)],
                    ["Reported speed", card.cloud.state === "syncing" && card.cloud.speed !== null ? root.size(card.cloud.speed) + "/s" : "—"],
                    ["Queue ETA", card.cloud.state === "syncing" ? (card.cloud.eta || "Not reported") : "—"]
                  ]
                  delegate: Row {
                    required property var modelData
                    width: (details.width - Style.space(12)) / 2
                    height: metric.implicitHeight
                    Column {
                      id: metric
                      width: parent.width
                      spacing: Style.space(2)
                      Text {
                        text: modelData[0]
                        color: root.muted
                        font.family: root.bar.fontFamily
                        font.pixelSize: Style.font.caption
                      }
                      Text {
                        text: modelData[1].toString()
                        color: root.bar.foreground
                        font.family: root.bar.fontFamily
                        font.pixelSize: Style.font.bodySmall
                        width: parent.width
                        elide: Text.ElideRight
                      }
                    }
                  }
                }
              }
              Column {
                width: parent.width
                spacing: Style.space(5)
                Text {
                  visible: card.cloud.state === "syncing" && !!card.cloud.elapsedText
                  width: parent.width
                  text: "Current run · " + (card.cloud.elapsedText || "")
                  color: root.bar.foreground
                  font.family: root.bar.fontFamily
                  font.pixelSize: Style.font.bodySmall
                }
                Text {
                  visible: !!card.cloud.lastSuccess
                  width: parent.width
                  text: "Last successful check · " + (card.cloud.lastSuccess ? Qt.formatDateTime(new Date(card.cloud.lastSuccess * 1000), "HH:mm") + " (" + root.age(card.cloud.lastSuccess) + ")" : "")
                  wrapMode: Text.Wrap
                  color: root.muted
                  font.family: root.bar.fontFamily
                  font.pixelSize: Style.font.caption
                }
                Text {
                  visible: !!card.cloud.verifiedInventory
                  width: parent.width
                  text: card.cloud.verifiedInventory ? "Verified inventory · " + card.cloud.verifiedInventory.localItems.toLocaleString(Qt.locale(), 'f', 0) + " local / " + card.cloud.verifiedInventory.remoteItems.toLocaleString(Qt.locale(), 'f', 0) + " cloud items" : ""
                  wrapMode: Text.Wrap
                  color: root.muted
                  font.family: root.bar.fontFamily
                  font.pixelSize: Style.font.caption
                }
              }
              Column {
                width: parent.width
                spacing: Style.space(8)
                visible: card.cloud.state === "syncing" && (card.cloud.activeFiles || []).length > 0
                Text {
                  text: "Transferring now"
                  color: root.muted
                  font.family: root.bar.fontFamily
                  font.pixelSize: Style.font.caption
                }
                Repeater {
                  model: (card.cloud.activeFiles || []).slice(0, 4)
                  delegate: Column {
                    id: activeFile
                    required property var modelData
                    width: details.width
                    spacing: Style.space(3)
                    Row {
                      width: parent.width
                      Text {
                        width: parent.width - Style.space(42)
                        text: activeFile.modelData.name
                        textFormat: Text.PlainText
                        elide: Text.ElideMiddle
                        color: root.bar.foreground
                        font.family: root.bar.fontFamily
                        font.pixelSize: Style.font.caption
                      }
                      Text {
                        width: Style.space(42)
                        text: activeFile.modelData.percentage === null ? "—" : activeFile.modelData.percentage + "%"
                        horizontalAlignment: Text.AlignRight
                        color: root.muted
                        font.family: root.bar.fontFamily
                        font.pixelSize: Style.font.caption
                      }
                    }
                    Rectangle {
                      width: parent.width
                      height: Style.space(2)
                      radius: height / 2
                      color: Qt.alpha(root.bar.foreground, 0.12)
                      Rectangle {
                        width: parent.width * (activeFile.modelData.percentage || 0) / 100
                        height: parent.height
                        radius: parent.radius
                        color: Commons.Color.accent
                      }
                    }
                  }
                }
              }
              Text {
                visible: !!card.cloud.lastFile
                width: parent.width
                text: "Last: " + (card.cloud.lastFile || "")
                textFormat: Text.PlainText
                elide: Text.ElideMiddle
                color: root.muted
                font.family: root.bar.fontFamily
                font.pixelSize: Style.font.caption
              }
              Text {
                width: parent.width
                text: (card.cloud.lastReport ? "Stats " + root.age(card.cloud.lastReport) : card.cloud.lastUpdate ? "Activity " + root.age(card.cloud.lastUpdate) : "") + (card.cloud.errors ? "  ·  " + card.cloud.errors + " error log entries" : "")
                visible: text !== ""
                color: card.cloud.errors ? root.bar.urgent : root.muted
                font.family: root.bar.fontFamily
                font.pixelSize: Style.font.caption
                wrapMode: Text.Wrap
              }
              Text {
                visible: card.cloud.state === "syncing"
                width: parent.width
                text: card.cloud.phase === "scanning" ? "Reading folder metadata. Verified inventory includes files and folders from the last successful check." : "Queue refills as files finish. Its count and ETA cover queued work, not the entire sync."
                wrapMode: Text.Wrap
                color: root.muted
                font.family: root.bar.fontFamily
                font.pixelSize: Style.font.caption
              }
              Text {
                visible: card.cloud.state === "synced"
                width: parent.width
                text: "Last check passed · automatic sync scheduled"
                color: root.muted
                font.family: root.bar.fontFamily
                font.pixelSize: Style.font.caption
                wrapMode: Text.Wrap
              }
            }
          }
        }
        Text {
          text: "Read-only monitor · updates every 5 seconds"
          color: root.muted
          font.family: root.bar.fontFamily
          font.pixelSize: Style.font.caption
        }
      }
    }
  }
}
