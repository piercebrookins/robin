import Foundation
import AppKit
import ScreenCaptureKit
import ApplicationServices
import AVFoundation
import CoreAudio
import AudioToolbox
import ImageIO
import UniformTypeIdentifiers
import Darwin

@main struct RobinMacHelper {
  static func main() async {
    do { try await run() }
    catch { FileHandle.standardError.write(Data("RobinMacHelper: \(error)\n".utf8)); Darwin.exit(1) }
  }
  static func run() async throws {
    if CommandLine.arguments.dropFirst().first == "audio-bridge" { try await AudioBridge.run(arguments: Array(CommandLine.arguments.dropFirst(2))); return }
    if CommandLine.arguments.dropFirst().first == "configure-audio" { try configureAudioRoutes(); return }
    let socketPath = argument("--socket") ?? ProcessInfo.processInfo.environment["ROBIN_HELPER_SOCKET"] ?? "/tmp/robin-helper.sock"
    try await RpcServer(path: socketPath).run()
  }
}

func argument(_ name: String, in args: [String] = CommandLine.arguments) -> String? {
  guard let index = args.firstIndex(of: name), args.indices.contains(index + 1) else { return nil }; return args[index + 1]
}

struct RpcServer {
  let path: String
  func run() async throws {
    unlink(path)
    let fd = socket(AF_UNIX, SOCK_STREAM, 0); guard fd >= 0 else { throw HelperError("socket failed") }
    var address = sockaddr_un(); address.sun_family = sa_family_t(AF_UNIX)
    let bytes = Array(path.utf8CString); guard bytes.count <= MemoryLayout.size(ofValue: address.sun_path) else { throw HelperError("socket path is too long") }
    withUnsafeMutablePointer(to: &address.sun_path) { ptr in ptr.withMemoryRebound(to: CChar.self, capacity: bytes.count) { destination in _ = bytes.withUnsafeBufferPointer { source in memcpy(destination, source.baseAddress, bytes.count) } } }
    let result = withUnsafePointer(to: &address) { $0.withMemoryRebound(to: sockaddr.self, capacity: 1) { bind(fd, $0, socklen_t(MemoryLayout<sockaddr_un>.size)) } }
    guard result == 0, listen(fd, 16) == 0 else { throw HelperError("bind/listen failed: \(String(cString: strerror(errno)))") }
    chmod(path, S_IRUSR | S_IWUSR)
    signal(SIGPIPE, SIG_IGN)
    while true {
      let client = accept(fd, nil, nil); if client < 0 { continue }
      Task.detached { await handleClient(client) }
    }
  }
}

func handleClient(_ fd: Int32) async {
  defer { close(fd) }; var bytes = [UInt8](); var byte: UInt8 = 0
  while read(fd, &byte, 1) == 1, byte != 10, bytes.count < 1_048_576 { bytes.append(byte) }
  guard let object = try? JSONSerialization.jsonObject(with: Data(bytes)) as? [String: Any], let id = object["id"] as? String, let method = object["method"] as? String else { return }
  do {
    let result = try await Helper.handle(method: method, params: object["params"] as? [String: Any] ?? [:])
    writeJSON(fd, ["id": id, "result": result])
  } catch { writeJSON(fd, ["id": id, "error": String(describing: error)]) }
}

func writeJSON(_ fd: Int32, _ value: [String: Any]) {
  guard var data = try? JSONSerialization.data(withJSONObject: value) else { return }; data.append(10)
  data.withUnsafeBytes { buffer in _ = Darwin.write(fd, buffer.baseAddress, data.count) }
}

enum Helper {
  nonisolated(unsafe) static var stopped = false
  static func handle(method: String, params: [String: Any]) async throws -> Any {
    switch method {
    case "permissions": return permissions()
    case "windows": return try await windows()
    case "screenshot": return try await screenshot(displayId: params["displayId"] as? UInt32)
    case "perform": return try await perform(params["actions"] as? [[String: Any]] ?? [])
    case "stop": stopped = true; releaseInput(); return ["ok": true]
    case "resume": stopped = false; return ["ok": true]
    default: throw HelperError("unknown method \(method)")
    }
  }

  static func permissions() -> [String: Bool] {
    ["screenRecording": CGPreflightScreenCaptureAccess(), "accessibility": AXIsProcessTrusted(), "inputMonitoring": CGPreflightListenEventAccess(), "microphone": AVCaptureDevice.authorizationStatus(for: .audio) == .authorized]
  }

  static func windows() async throws -> [[String: Any]] {
    let content = try await SCShareableContent.excludingDesktopWindows(false, onScreenWindowsOnly: false)
    let frontPid = NSWorkspace.shared.frontmostApplication?.processIdentifier
    return content.windows.map { window in
      let frame = window.frame; let app = window.owningApplication
      return ["id": Int(window.windowID), "owner": app?.applicationName ?? "", "bundleId": app?.bundleIdentifier ?? "", "title": window.title ?? "", "bounds": ["x": frame.origin.x, "y": frame.origin.y, "width": frame.width, "height": frame.height], "focused": app?.processID == frontPid, "onScreen": window.isOnScreen]
    }
  }

  static func screenshot(displayId: UInt32?) async throws -> [String: Any] {
    let content = try await SCShareableContent.excludingDesktopWindows(false, onScreenWindowsOnly: true)
    guard let display = displayId.flatMap({ id in content.displays.first(where: { $0.displayID == id }) }) ?? content.displays.first else { throw HelperError("no display available") }
    let filter = SCContentFilter(display: display, excludingWindows: [])
    let config = SCStreamConfiguration(); config.width = display.width; config.height = display.height; config.showsCursor = true; config.captureResolution = .best
    let image = try await SCScreenshotManager.captureImage(contentFilter: filter, configuration: config)
    let data = NSMutableData(); guard let destination = CGImageDestinationCreateWithData(data, UTType.png.identifier as CFString, 1, nil) else { throw HelperError("PNG encoder unavailable") }
    CGImageDestinationAddImage(destination, image, nil); guard CGImageDestinationFinalize(destination) else { throw HelperError("PNG encoding failed") }
    return ["mime": "image/png", "width": image.width, "height": image.height, "data": (data as Data).base64EncodedString(), "capturedAt": ISO8601DateFormatter().string(from: Date()), "displayId": display.displayID]
  }

  static func perform(_ actions: [[String: Any]]) async throws -> [String: Any] {
    if stopped { return ["accepted": false, "completed": 0, "stopped": true] }; var completed = 0
    for action in actions {
      if stopped { return ["accepted": false, "completed": completed, "stopped": true] }
      try await performOne(action); completed += 1
    }
    return ["accepted": true, "completed": completed]
  }

  static func performOne(_ action: [String: Any]) async throws {
    guard let type = action["type"] as? String else { throw HelperError("action type missing") }
    switch type {
    case "screenshot": _ = try await screenshot(displayId: nil)
    case "open_url": guard let value = action["url"] as? String, let url = URL(string: value), url.scheme == "https" else { throw HelperError("invalid URL") }; guard NSWorkspace.shared.open(url) else { throw HelperError("macOS could not open URL") }
    case "wait": try await Task.sleep(for: .milliseconds(action["ms"] as? Int ?? 1000))
    case "move": postMouse(.mouseMoved, action)
    case "click", "double_click":
      let point = point(action), button = mouseButton(action["button"] as? String); let down: CGEventType = button == .right ? .rightMouseDown : .leftMouseDown; let up: CGEventType = button == .right ? .rightMouseUp : .leftMouseUp
      let count = type == "double_click" ? 2 : 1
      for click in 1...count { let d = CGEvent(mouseEventSource: nil, mouseType: down, mouseCursorPosition: point, mouseButton: button)!; d.setIntegerValueField(.mouseEventClickState, value: Int64(click)); d.post(tap: .cghidEventTap); let u = CGEvent(mouseEventSource: nil, mouseType: up, mouseCursorPosition: point, mouseButton: button)!; u.setIntegerValueField(.mouseEventClickState, value: Int64(click)); u.post(tap: .cghidEventTap) }
    case "scroll": CGEvent(scrollWheelEvent2Source: nil, units: .pixel, wheelCount: 2, wheel1: Int32(action["scrollY"] as? Int ?? 0), wheel2: Int32(action["scrollX"] as? Int ?? 0), wheel3: 0)?.post(tap: .cghidEventTap)
    case "type": typeText(action["text"] as? String ?? "")
    case "keypress": keypress(action["keys"] as? [String] ?? [])
    case "drag": try await drag(action["path"] as? [[String: Any]] ?? [])
    case "semantic": try semantic(action)
    default: throw HelperError("unsupported action \(type)")
    }
  }

  static func semantic(_ action: [String: Any]) throws {
    guard AXIsProcessTrusted() else { throw HelperError("Accessibility permission is not granted") }
    let bundle = action["app"] as? String ?? ""; guard let app = NSRunningApplication.runningApplications(withBundleIdentifier: bundle).first else { throw HelperError("app \(bundle) is not running") }
    app.activate(options: [.activateAllWindows]); let root = AXUIElementCreateApplication(app.processIdentifier)
    if action["role"] as? String == "application" { return }
    guard let element = findElement(root, role: action["role"] as? String, title: action["title"] as? String, depth: 0) else { throw HelperError("Accessibility element not found") }
    switch action["action"] as? String {
    case "focus": AXUIElementSetAttributeValue(element, kAXFocusedAttribute as CFString, kCFBooleanTrue)
    case "set_value": AXUIElementSetAttributeValue(element, kAXValueAttribute as CFString, (action["value"] as? String ?? "") as CFTypeRef)
    default: let status = AXUIElementPerformAction(element, kAXPressAction as CFString); if status != .success { throw HelperError("Accessibility press failed: \(status.rawValue)") }
    }
  }
}

func findElement(_ element: AXUIElement, role: String?, title: String?, depth: Int) -> AXUIElement? {
  if depth > 12 { return nil }; var roleValue: CFTypeRef?; var titleValue: CFTypeRef?
  AXUIElementCopyAttributeValue(element, kAXRoleAttribute as CFString, &roleValue); AXUIElementCopyAttributeValue(element, kAXTitleAttribute as CFString, &titleValue)
  let actualRole = roleValue as? String ?? ""; let actualTitle = titleValue as? String ?? ""
  let roleMatches = role == nil || actualRole.localizedCaseInsensitiveContains(role!) || actualRole.replacingOccurrences(of: "AX", with: "").localizedCaseInsensitiveContains(role!)
  let titleMatches = title == nil || actualTitle.localizedCaseInsensitiveContains(title!)
  if roleMatches && titleMatches { return element }
  var childrenValue: CFTypeRef?; guard AXUIElementCopyAttributeValue(element, kAXChildrenAttribute as CFString, &childrenValue) == .success, let children = childrenValue as? [AXUIElement] else { return nil }
  for child in children { if let found = findElement(child, role: role, title: title, depth: depth + 1) { return found } }; return nil
}

func point(_ action: [String: Any]) -> CGPoint { CGPoint(x: action["x"] as? Double ?? Double(action["x"] as? Int ?? 0), y: action["y"] as? Double ?? Double(action["y"] as? Int ?? 0)) }
func mouseButton(_ value: String?) -> CGMouseButton { value == "right" ? .right : .left }
func postMouse(_ type: CGEventType, _ action: [String: Any]) { CGEvent(mouseEventSource: nil, mouseType: type, mouseCursorPosition: point(action), mouseButton: .left)?.post(tap: .cghidEventTap) }
func typeText(_ text: String) { for scalar in text.unicodeScalars { var value = UInt16(scalar.value); CGEvent(keyboardEventSource: nil, virtualKey: 0, keyDown: true)?.tap { $0.keyboardSetUnicodeString(stringLength: 1, unicodeString: &value); $0.post(tap: .cghidEventTap) }; CGEvent(keyboardEventSource: nil, virtualKey: 0, keyDown: false)?.tap { $0.keyboardSetUnicodeString(stringLength: 1, unicodeString: &value); $0.post(tap: .cghidEventTap) } } }
func keypress(_ keys: [String]) { let flags = flags(keys); for key in keys where !["CMD","COMMAND","CTRL","CONTROL","ALT","OPTION","SHIFT"].contains(key.uppercased()) { guard let code = keyCodes[key.uppercased()] else { continue }; let down = CGEvent(keyboardEventSource: nil, virtualKey: code, keyDown: true); down?.flags = flags; down?.post(tap: .cghidEventTap); let up = CGEvent(keyboardEventSource: nil, virtualKey: code, keyDown: false); up?.flags = flags; up?.post(tap: .cghidEventTap) } }
func flags(_ keys: [String]) -> CGEventFlags { var f: CGEventFlags = []; for key in keys.map({$0.uppercased()}) { if ["CMD","COMMAND"].contains(key){f.insert(.maskCommand)};if ["CTRL","CONTROL"].contains(key){f.insert(.maskControl)};if ["ALT","OPTION"].contains(key){f.insert(.maskAlternate)};if key=="SHIFT"{f.insert(.maskShift)} };return f }
let keyCodes: [String: CGKeyCode] = ["A":0,"S":1,"D":2,"F":3,"H":4,"G":5,"Z":6,"X":7,"C":8,"V":9,"B":11,"Q":12,"W":13,"E":14,"R":15,"Y":16,"T":17,"1":18,"2":19,"3":20,"4":21,"6":22,"5":23,"=":24,"9":25,"7":26,"-":27,"8":28,"0":29,"]":30,"O":31,"U":32,"[":33,"I":34,"P":35,"ENTER":36,"L":37,"J":38,"'":39,"K":40,";":41,"\\":42,",":43,"/":44,"N":45,"M":46,".":47,"TAB":48,"SPACE":49,"BACKSPACE":51,"ESC":53,"LEFT":123,"RIGHT":124,"DOWN":125,"UP":126]
func drag(_ path: [[String: Any]]) async throws { guard let first = path.first else { return }; let start = point(first); CGEvent(mouseEventSource: nil, mouseType: .leftMouseDown, mouseCursorPosition: start, mouseButton: .left)?.post(tap: .cghidEventTap); for item in path.dropFirst() { CGEvent(mouseEventSource: nil, mouseType: .leftMouseDragged, mouseCursorPosition: point(item), mouseButton: .left)?.post(tap: .cghidEventTap); try await Task.sleep(for: .milliseconds(8)) }; CGEvent(mouseEventSource: nil, mouseType: .leftMouseUp, mouseCursorPosition: point(path.last!), mouseButton: .left)?.post(tap: .cghidEventTap) }
func releaseInput() { let p = CGEvent(source: nil)?.location ?? .zero; CGEvent(mouseEventSource: nil, mouseType: .leftMouseUp, mouseCursorPosition: p, mouseButton: .left)?.post(tap: .cghidEventTap); CGEvent(mouseEventSource: nil, mouseType: .rightMouseUp, mouseCursorPosition: p, mouseButton: .right)?.post(tap: .cghidEventTap) }
extension CGEvent { func tap(_ body: (CGEvent) -> Void) { body(self) } }
struct HelperError: Error, CustomStringConvertible { let description: String; init(_ description: String) { self.description = description } }

func configureAudioRoutes() throws {
  try createAggregate(name: "Robin Speaker", uid: "com.robin.audio.speaker", subdeviceName: "BlackHole 2ch")
  try createAggregate(name: "Robin Microphone", uid: "com.robin.audio.microphone", subdeviceName: "BlackHole 16ch")
  FileHandle.standardOutput.write(Data("Configured Robin Speaker and Robin Microphone\n".utf8))
}

func createAggregate(name: String, uid: String, subdeviceName: String) throws {
  guard let subdevice = findAudioDevice(named: subdeviceName, input: true), let subUID = deviceUID(subdevice) else { throw HelperError("required device not found: \(subdeviceName)") }
  if let existing = findAudioDeviceByUID(uid) { _ = AudioHardwareDestroyAggregateDevice(existing) }
  let description: [String: Any] = [
    kAudioAggregateDeviceNameKey: name, kAudioAggregateDeviceUIDKey: uid,
    kAudioAggregateDeviceSubDeviceListKey: [[kAudioSubDeviceUIDKey: subUID]],
    kAudioAggregateDeviceMainSubDeviceKey: subUID,
    kAudioAggregateDeviceIsPrivateKey: false,
    kAudioAggregateDeviceIsStackedKey: false
  ]
  var aggregate = AudioDeviceID(0); let status = AudioHardwareCreateAggregateDevice(description as CFDictionary, &aggregate)
  guard status == noErr else { throw HelperError("creating \(name) failed: \(status)") }
}

func deviceUID(_ device: AudioDeviceID) -> String? {
  var address = AudioObjectPropertyAddress(mSelector: kAudioDevicePropertyDeviceUID, mScope: kAudioObjectPropertyScopeGlobal, mElement: kAudioObjectPropertyElementMain)
  var value: Unmanaged<CFString>?; var size = UInt32(MemoryLayout<Unmanaged<CFString>?>.size)
  guard AudioObjectGetPropertyData(device, &address, 0, nil, &size, &value) == noErr, let value else { return nil }; return value.takeUnretainedValue() as String
}

func findAudioDeviceByUID(_ uid: String) -> AudioDeviceID? {
  var cfUID: CFString = uid as CFString; var output = AudioDeviceID(0)
  let status: OSStatus = withUnsafeMutablePointer(to: &cfUID) { inputPointer in
    withUnsafeMutablePointer(to: &output) { outputPointer in
      var translation = AudioValueTranslation(mInputData: inputPointer, mInputDataSize: UInt32(MemoryLayout<CFString>.size), mOutputData: outputPointer, mOutputDataSize: UInt32(MemoryLayout<AudioDeviceID>.size))
      var address = AudioObjectPropertyAddress(mSelector: kAudioHardwarePropertyDeviceForUID, mScope: kAudioObjectPropertyScopeGlobal, mElement: kAudioObjectPropertyElementMain); var size = UInt32(MemoryLayout<AudioValueTranslation>.size)
      return AudioObjectGetPropertyData(AudioObjectID(kAudioObjectSystemObject), &address, 0, nil, &size, &translation)
    }
  }
  return status == noErr && output != 0 ? output : nil
}
