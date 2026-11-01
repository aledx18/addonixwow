import QtQuick
import QtQuick.Controls as Controls
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Ui

Panel {
  id: root
  moduleName: "io.github.aledx18.addonixwow"
  manageIpc: false

  property var anchorItem: null
  property var hostWidget: null

  readonly property color panelForeground: Color.foreground
  readonly property string fontFamily: root.bar ? root.bar.fontFamily : Style.font.family
  readonly property string backend: Qt.resolvedUrl("backend/addonix.py").toString().replace("file://", "")
  readonly property string keysUrl: "https://console.curseforge.com/"

  property string addonsPath: ""
  property bool hasKey: false
  property bool statusLoaded: false
  property var installed: []
  property var results: []
  property string view: "browse"
  property string query: ""
  property string searchedQuery: ""
  property bool searching: false
  property bool searchLoaded: false
  property bool searchLimited: false
  property string pendingQuery: ""
  property bool searchQueued: false
  property string errorMessage: ""
  property string errorUrl: ""
  property string noticeMessage: ""
  property int installingId: 0
  readonly property bool installing: installingId !== 0
  property bool savingKey: false

  function open() {
    root.controller.show()
    root.refreshStatus()
  }

  function close() {
    root.controller.hide()
  }

  function toggle() {
    if (root.opened) root.close()
    else root.open()
  }

  function closeForPopoutSwitch() {
    root.close()
  }

  function switchPanel(direction) {
    if (root.bar && typeof root.bar.switchPanelFrom === "function")
      return root.bar.switchPanelFrom(root.hostWidget || root, direction)
    return false
  }

  function parse(raw) {
    try {
      return JSON.parse(raw)
    } catch (error) {
      return { ok: false, code: "backend", message: "The addon backend returned an unexpected response." }
    }
  }

  function showError(result) {
    root.errorMessage = result.message || "Something went wrong."
    root.errorUrl = result.url || ""
    root.noticeMessage = ""
  }

  function clearMessages() {
    root.errorMessage = ""
    root.errorUrl = ""
    root.noticeMessage = ""
  }

  function refreshStatus() {
    if (statusProcess.running) return
    statusProcess.command = ["python3", "-I", root.backend, "status"]
    statusProcess.running = true
  }

  function search(text) {
    if (!root.hasKey) return
    // A running search cannot be retargeted, so the newest text waits for it to end.
    root.pendingQuery = text
    if (searchProcess.running) {
      root.searchQueued = true
      return
    }
    root.searchQueued = false
    root.searching = true
    root.errorMessage = ""
    root.errorUrl = ""
    root.searchedQuery = text
    searchProcess.command = ["python3", "-I", root.backend, "search", text]
    searchProcess.running = true
  }

  function install(modId) {
    if (root.installing) return
    root.clearMessages()
    root.installingId = modId
    installProcess.command = ["python3", "-I", root.backend, "install", String(modId)]
    installProcess.running = true
  }

  function saveKey(key) {
    var trimmed = String(key || "").trim()
    if (!trimmed || keyProcess.running) return
    root.clearMessages()
    root.savingKey = true
    keyProcess.secret = trimmed
    keyProcess.command = ["python3", "-I", root.backend, "set-key"]
    keyProcess.running = true
  }

  function shellQuote(value) {
    return "'" + String(value).replace(/'/g, "'\\''") + "'"
  }

  function openExternal(target) {
    if (!target) return
    Util.execDetached("xdg-open " + root.shellQuote(target))
  }

  function formatDownloads(count) {
    if (count >= 1000000) return (count / 1000000).toFixed(1) + "M"
    if (count >= 1000) return Math.round(count / 1000) + "K"
    return String(count)
  }

  function markInstalled(modId) {
    root.results = root.results.map(function(item) {
      return item.id === modId ? Object.assign({}, item, { installed: true }) : item
    })
  }

  Process {
    id: statusProcess
    command: []
    stdout: StdioCollector { id: statusOutput; waitForEnd: true }
    onExited: {
      var result = root.parse(statusOutput.text)
      if (!result.ok) {
        root.showError(result)
        return
      }
      root.statusLoaded = true
      root.addonsPath = result.addonsPath
      root.hasKey = result.hasKey
      root.installed = result.installed
      if (root.hasKey && !root.searchLoaded) root.search(root.query)
    }
  }

  Process {
    id: searchProcess
    command: []
    stdout: StdioCollector { id: searchOutput; waitForEnd: true }
    onExited: {
      root.searching = false
      var result = root.parse(searchOutput.text)
      if (result.ok) {
        root.results = result.results
        root.searchLimited = result.limited === true
        root.searchLoaded = true
      } else {
        root.results = []
        root.showError(result)
        if (result.code === "invalid_key" || result.code === "missing_key") root.hasKey = false
      }
      if (root.searchQueued && root.pendingQuery !== root.searchedQuery) Qt.callLater(root.search, root.pendingQuery)
      root.searchQueued = false
    }
  }

  Process {
    id: installProcess
    command: []
    stdout: StdioCollector { id: installOutput; waitForEnd: true }
    onExited: {
      var modId = root.installingId
      root.installingId = 0
      var result = root.parse(installOutput.text)
      if (result.ok) {
        root.noticeMessage = "Installed " + result.name + ". Reload the game UI with /reload."
        root.markInstalled(modId)
        root.refreshStatus()
        Util.execDetached("notify-send AddonixWoW " + root.shellQuote("Installed " + result.name))
      } else {
        root.showError(result)
      }
    }
  }

  Process {
    id: keyProcess
    // The key goes over stdin, never argv, so it stays out of the process list.
    property string secret: ""
    command: []
    stdinEnabled: true
    stdout: StdioCollector { id: keyOutput; waitForEnd: true }
    onStarted: {
      write(secret + "\n")
      secret = ""
    }
    onExited: {
      root.savingKey = false
      var result = root.parse(keyOutput.text)
      if (result.ok) {
        keyField.text = ""
        root.refreshStatus()
      } else {
        root.showError(result)
      }
    }
  }

  Timer {
    id: searchDebounce
    interval: 450
    onTriggered: root.search(root.query)
  }

  Component.onCompleted: root.refreshStatus()

  KeyboardPanel {
    id: panel
    anchorItem: root.anchorItem
    owner: root.hostWidget || root
    bar: root.bar
    open: root.opened
    focusTarget: keyCatcher
    contentWidth: panel.fittedContentWidth(Style.space(460))
    contentHeight: panel.fittedContentHeight(content.implicitHeight)

    PanelKeyCatcher {
      id: keyCatcher
      anchors.fill: parent
      onCloseRequested: root.close()
      onTabRequested: function(direction) {
        root.switchPanel(direction)
      }

      Column {
        id: content
        width: parent.width
        spacing: Style.space(10)

        Item {
          width: parent.width
          height: Style.space(36)

          Column {
            anchors.left: parent.left
            anchors.right: folderButton.left
            anchors.rightMargin: Style.space(10)
            anchors.verticalCenter: parent.verticalCenter
            spacing: Style.space(2)

            Text {
              width: parent.width
              text: "AddonixWoW"
              color: root.panelForeground
              font.family: root.fontFamily
              font.pixelSize: Style.font.heading
              font.bold: true
              elide: Text.ElideRight
            }

            Text {
              width: parent.width
              text: root.addonsPath ? "World of Warcraft: Forever · " + root.installed.length + " installed" : "World of Warcraft: Forever"
              color: root.panelForeground
              opacity: 0.65
              font.family: root.fontFamily
              font.pixelSize: Style.font.caption
              elide: Text.ElideRight
            }
          }

          Button {
            id: folderButton
            anchors.right: parent.right
            anchors.verticalCenter: parent.verticalCenter
            text: "󰉋 Open folder"
            bordered: true
            selected: false
            foreground: root.panelForeground
            fontSize: Style.font.caption
            horizontalPadding: Style.space(10)
            verticalPadding: Style.space(4)
            enabled: root.addonsPath.length > 0
            opacity: enabled ? 1 : 0.5
            tooltipText: root.addonsPath
            onClicked: {
              root.openExternal(root.addonsPath)
              root.close()
            }
          }
        }

        Rectangle {
          width: parent.width
          height: 1
          color: root.panelForeground
          opacity: 0.15
        }

        Text {
          width: parent.width
          visible: root.statusLoaded && root.addonsPath.length === 0
          text: "No WoW Forever install found. AddonixWoW looks in your Faugus prefixes; set \"addonsPath\" in ~/.config/addonixwow/config.json for a different location."
          color: root.panelForeground
          font.family: root.fontFamily
          font.pixelSize: Style.font.bodySmall
          wrapMode: Text.WordWrap
        }

        // First run: searching needs the user's own CurseForge key.
        Column {
          width: parent.width
          spacing: Style.space(8)
          visible: root.statusLoaded && !root.hasKey

          Text {
            width: parent.width
            text: "CurseForge requires a personal API key to search and download addons."
            color: root.panelForeground
            font.family: root.fontFamily
            font.pixelSize: Style.font.body
            wrapMode: Text.WordWrap
          }

          TextField {
            id: keyField
            width: parent.width
            password: true
            placeholderText: "Paste your CurseForge API key"
            foreground: root.panelForeground
            onAccepted: root.saveKey(text)
          }

          Row {
            spacing: Style.space(6)

            Button {
              text: root.savingKey ? "Checking…" : "Save key"
              bordered: true
              selected: false
              foreground: root.panelForeground
              fontSize: Style.font.caption
              horizontalPadding: Style.space(10)
              verticalPadding: Style.space(4)
              enabled: !root.savingKey && keyField.text.length > 0
              opacity: enabled ? 1 : 0.5
              onClicked: root.saveKey(keyField.text)
            }

            Button {
              text: "Get a key"
              bordered: true
              selected: false
              foreground: root.panelForeground
              fontSize: Style.font.caption
              horizontalPadding: Style.space(10)
              verticalPadding: Style.space(4)
              onClicked: root.openExternal(root.keysUrl)
            }
          }
        }

        Row {
          spacing: Style.space(6)
          visible: root.statusLoaded

          Button {
            text: "Browse"
            bordered: true
            selected: root.view === "browse"
            visible: root.hasKey
            foreground: root.panelForeground
            fontSize: Style.font.caption
            horizontalPadding: Style.space(10)
            verticalPadding: Style.space(4)
            onClicked: root.view = "browse"
          }

          Button {
            text: "Installed (" + root.installed.length + ")"
            bordered: true
            selected: root.view === "installed" || !root.hasKey
            foreground: root.panelForeground
            fontSize: Style.font.caption
            horizontalPadding: Style.space(10)
            verticalPadding: Style.space(4)
            onClicked: root.view = "installed"
          }
        }

        TextField {
          id: searchField
          width: parent.width
          visible: root.hasKey && root.view === "browse"
          placeholderText: "Search CurseForge addons…"
          foreground: root.panelForeground
          onTextChanged: {
            root.query = text
            searchDebounce.restart()
          }
          onAccepted: {
            searchDebounce.stop()
            root.search(text)
          }
        }

        Text {
          width: parent.width
          visible: root.errorMessage.length > 0
          text: "󰀦 " + root.errorMessage
          color: Color.urgent
          font.family: root.fontFamily
          font.pixelSize: Style.font.bodySmall
          wrapMode: Text.WordWrap
        }

        Button {
          visible: root.errorUrl.length > 0
          text: "Open on CurseForge"
          bordered: true
          selected: false
          foreground: root.panelForeground
          fontSize: Style.font.caption
          horizontalPadding: Style.space(10)
          verticalPadding: Style.space(4)
          onClicked: root.openExternal(root.errorUrl)
        }

        Text {
          width: parent.width
          visible: root.noticeMessage.length > 0
          text: "󰄬 " + root.noticeMessage
          color: root.panelForeground
          font.family: root.fontFamily
          font.pixelSize: Style.font.bodySmall
          wrapMode: Text.WordWrap
        }

        Text {
          width: parent.width
          visible: root.view === "browse" && root.hasKey && root.searchLimited
          text: "CurseForge blocks full search for this key. Showing featured WoW Forever addons only."
          color: root.panelForeground
          opacity: 0.65
          font.family: root.fontFamily
          font.pixelSize: Style.font.caption
          wrapMode: Text.WordWrap
        }

        Text {
          width: parent.width
          visible: root.view === "browse" && root.hasKey && (root.searching || (root.searchLoaded && root.results.length === 0 && root.errorMessage.length === 0))
          text: root.searching ? "Searching…" : "No WoW Forever addons match your search."
          color: root.panelForeground
          opacity: 0.65
          font.family: root.fontFamily
          font.pixelSize: Style.font.bodySmall
        }

        Controls.ScrollView {
          id: browseScroll
          width: parent.width
          height: Math.min(browseColumn.implicitHeight, Style.space(420))
          visible: root.view === "browse" && root.hasKey && root.results.length > 0
          clip: true
          contentWidth: availableWidth
          Controls.ScrollBar.horizontal.policy: Controls.ScrollBar.AlwaysOff

          Column {
            id: browseColumn
            width: browseScroll.availableWidth
            spacing: Style.space(6)

            Repeater {
              model: root.results

              BorderSurface {
                id: resultCard
                required property var modelData
                width: browseColumn.width
                height: Style.space(64)
                radius: Style.space(8)
                color: "transparent"
                borderSpec: Border.controlSpec("normal", root.panelForeground, Color.accent)

                Image {
                  id: logo
                  anchors.left: parent.left
                  anchors.leftMargin: Style.space(10)
                  anchors.verticalCenter: parent.verticalCenter
                  width: Style.space(40)
                  height: Style.space(40)
                  source: resultCard.modelData.logo || ""
                  sourceSize.width: Style.space(80)
                  sourceSize.height: Style.space(80)
                  fillMode: Image.PreserveAspectFit
                  asynchronous: true
                }

                Column {
                  anchors.left: logo.right
                  anchors.leftMargin: Style.space(10)
                  anchors.right: installButton.left
                  anchors.rightMargin: Style.space(10)
                  anchors.verticalCenter: parent.verticalCenter
                  spacing: Style.space(2)

                  Text {
                    width: parent.width
                    text: resultCard.modelData.name
                    color: root.panelForeground
                    font.family: root.fontFamily
                    font.pixelSize: Style.font.body
                    font.bold: true
                    elide: Text.ElideRight
                  }

                  Text {
                    width: parent.width
                    text: resultCard.modelData.summary
                    color: root.panelForeground
                    opacity: 0.65
                    font.family: root.fontFamily
                    font.pixelSize: Style.font.caption
                    elide: Text.ElideRight
                  }

                  Text {
                    width: parent.width
                    text: "󰇚 " + root.formatDownloads(resultCard.modelData.downloads)
                    color: root.panelForeground
                    opacity: 0.5
                    font.family: root.fontFamily
                    font.pixelSize: Style.font.caption
                  }
                }

                Button {
                  id: installButton
                  anchors.right: parent.right
                  anchors.rightMargin: Style.space(10)
                  anchors.verticalCenter: parent.verticalCenter
                  text: !resultCard.modelData.canInstall ? "Open page" : (root.installingId === resultCard.modelData.id ? "Installing…" : (resultCard.modelData.installed ? "Reinstall" : "Install"))
                  bordered: true
                  selected: false
                  foreground: root.panelForeground
                  fontSize: Style.font.caption
                  horizontalPadding: Style.space(10)
                  verticalPadding: Style.space(4)
                  enabled: !resultCard.modelData.canInstall || (!root.installing && root.addonsPath.length > 0)
                  opacity: enabled ? 1 : 0.6
                  tooltipText: resultCard.modelData.canInstall ? "" : "The author only allows downloads from CurseForge"
                  onClicked: {
                    if (resultCard.modelData.canInstall) root.install(resultCard.modelData.id)
                    else root.openExternal(resultCard.modelData.url)
                  }
                }
              }
            }
          }
        }

        Text {
          width: parent.width
          visible: root.statusLoaded && (root.view === "installed" || !root.hasKey) && root.installed.length === 0
          text: "No addons installed yet."
          color: root.panelForeground
          opacity: 0.65
          font.family: root.fontFamily
          font.pixelSize: Style.font.bodySmall
        }

        Controls.ScrollView {
          id: installedScroll
          width: parent.width
          height: Math.min(installedColumn.implicitHeight, Style.space(420))
          visible: (root.view === "installed" || !root.hasKey) && root.installed.length > 0
          clip: true
          contentWidth: availableWidth
          Controls.ScrollBar.horizontal.policy: Controls.ScrollBar.AlwaysOff

          Column {
            id: installedColumn
            width: installedScroll.availableWidth
            spacing: Style.space(4)

            Repeater {
              model: root.installed

              Item {
                id: installedRow
                required property var modelData
                width: installedColumn.width
                height: Style.space(30)

                Text {
                  anchors.left: parent.left
                  anchors.right: installedVersion.left
                  anchors.rightMargin: Style.space(8)
                  anchors.verticalCenter: parent.verticalCenter
                  text: installedRow.modelData.name
                  color: root.panelForeground
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.bodySmall
                  elide: Text.ElideRight
                }

                Text {
                  id: installedVersion
                  anchors.right: openAddon.left
                  anchors.rightMargin: Style.space(8)
                  anchors.verticalCenter: parent.verticalCenter
                  width: Math.min(implicitWidth, Style.space(140))
                  text: installedRow.modelData.version
                  color: root.panelForeground
                  opacity: 0.55
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.caption
                  elide: Text.ElideRight
                }

                Button {
                  id: openAddon
                  anchors.right: parent.right
                  anchors.verticalCenter: parent.verticalCenter
                  text: "󰉋"
                  bordered: true
                  selected: false
                  foreground: root.panelForeground
                  fontSize: Style.font.caption
                  horizontalPadding: Style.space(8)
                  verticalPadding: Style.space(3)
                  tooltipText: "Open addon folder"
                  onClicked: {
                    root.openExternal(root.addonsPath + "/" + installedRow.modelData.folders[0])
                    root.close()
                  }
                }
              }
            }
          }
        }
      }
    }
  }
}
