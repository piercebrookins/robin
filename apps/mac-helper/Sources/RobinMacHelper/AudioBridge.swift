import Foundation
import AVFoundation
import CoreAudio
import AudioToolbox
import Darwin

enum AudioBridge {
  static func run(arguments: [String]) async throws {
    let inputName = argument("--input", in: arguments) ?? "Robin Speaker"
    let outputName = argument("--output", in: arguments) ?? "Robin Microphone"
    let rate = Double(argument("--rate", in: arguments) ?? "24000") ?? 24000
    guard let inputId = findAudioDevice(named: inputName, input: true) else { throw HelperError("input audio device not found: \(inputName)") }
    guard let outputId = findAudioDevice(named: outputName, input: false) else { throw HelperError("output audio device not found: \(outputName)") }

    let engine = AVAudioEngine(); let player = AVAudioPlayerNode(); engine.attach(player)
    try setDevice(engine.inputNode, id: inputId); try setDevice(engine.outputNode, id: outputId)
    guard let transport = AVAudioFormat(commonFormat: .pcmFormatInt16, sampleRate: rate, channels: 1, interleaved: false) else { throw HelperError("cannot create transport audio format") }
    engine.connect(player, to: engine.mainMixerNode, format: transport)
    let inputFormat = engine.inputNode.inputFormat(forBus: 0)
    guard let converter = AVAudioConverter(from: inputFormat, to: transport) else { throw HelperError("cannot create capture converter") }
    let output = BoundedPipeWriter(output: FileHandle.standardOutput, maxBytes: Int(rate * 2))
    engine.inputNode.installTap(onBus: 0, bufferSize: 960, format: inputFormat) { buffer, _ in
      let ratio = transport.sampleRate / inputFormat.sampleRate
      guard let converted = AVAudioPCMBuffer(pcmFormat: transport, frameCapacity: AVAudioFrameCount(Double(buffer.frameLength) * ratio + 8)) else { return }
      var supplied = false; var error: NSError?
      converter.convert(to: converted, error: &error) { _, status in
        if supplied { status.pointee = .noDataNow; return nil }; supplied = true; status.pointee = .haveData; return buffer
      }
      guard error == nil, converted.frameLength > 0, let samples = converted.int16ChannelData?[0] else { return }
      output.enqueue(Data(bytes: samples, count: Int(converted.frameLength) * MemoryLayout<Int16>.size))
    }
    let playbackGate = PlaybackGate(maxFrames: Int(rate * 0.75))
    signal(SIGUSR1, SIG_IGN)
    let interruption = DispatchSource.makeSignalSource(signal: SIGUSR1, queue: DispatchQueue(label: "com.robin.audio.interrupt", qos: .userInteractive))
    interruption.setEventHandler { player.stop(); playbackGate.clear(); player.play() }; interruption.resume()
    try engine.start(); player.play()
    FileHandle.standardError.write(Data("audio bridge ready input=\(inputName) output=\(outputName) rate=\(Int(rate))\n".utf8))

    while true {
      let data = FileHandle.standardInput.readData(ofLength: 8192); if data.isEmpty { break }
      let frames = data.count / MemoryLayout<Int16>.size
      guard frames > 0, let buffer = AVAudioPCMBuffer(pcmFormat: transport, frameCapacity: AVAudioFrameCount(frames)), let target = buffer.int16ChannelData?[0] else { continue }
      data.withUnsafeBytes { raw in if let base = raw.baseAddress { memcpy(target, base, frames * MemoryLayout<Int16>.size) } }
      buffer.frameLength = AVAudioFrameCount(frames); playbackGate.reserve(frames); await player.scheduleBuffer(buffer, at: nil, options: []); playbackGate.release(frames)
    }
    interruption.cancel(); engine.inputNode.removeTap(onBus: 0); player.stop(); engine.stop()
  }
}

final class BoundedPipeWriter: @unchecked Sendable {
  private let output: FileHandle, maxBytes: Int, lock = NSLock(), queue = DispatchQueue(label: "com.robin.audio.capture")
  private var chunks: [Data] = [], bytes = 0, draining = false
  init(output: FileHandle, maxBytes: Int) { self.output = output; self.maxBytes = maxBytes }
  func enqueue(_ data: Data) {
    lock.lock(); chunks.append(data); bytes += data.count
    while bytes > maxBytes, !chunks.isEmpty { bytes -= chunks.removeFirst().count }
    let shouldStart = !draining; if shouldStart { draining = true }; lock.unlock()
    if shouldStart { queue.async { self.drain() } }
  }
  private func drain() {
    while true {
      lock.lock()
      guard !chunks.isEmpty else { draining = false; lock.unlock(); return }
      let data = chunks.removeFirst(); bytes -= data.count; lock.unlock(); output.write(data)
    }
  }
}

final class PlaybackGate: @unchecked Sendable {
  private let condition = NSCondition(), maxFrames: Int; private var queuedFrames = 0
  init(maxFrames: Int) { self.maxFrames = maxFrames }
  func reserve(_ frames: Int) { condition.lock(); while queuedFrames + frames > maxFrames { condition.wait() }; queuedFrames += frames; condition.unlock() }
  func release(_ frames: Int) { condition.lock(); queuedFrames = max(0, queuedFrames - frames); condition.broadcast(); condition.unlock() }
  func clear() { condition.lock(); queuedFrames = 0; condition.broadcast(); condition.unlock() }
}

func setDevice(_ node: AVAudioIONode, id: AudioDeviceID) throws {
  guard let unit = node.audioUnit else { throw HelperError("audio unit unavailable") }; var device = id
  let status = AudioUnitSetProperty(unit, kAudioOutputUnitProperty_CurrentDevice, kAudioUnitScope_Global, 0, &device, UInt32(MemoryLayout<AudioDeviceID>.size))
  guard status == noErr else { throw HelperError("setting Core Audio device failed: \(status)") }
}

func findAudioDevice(named name: String, input: Bool) -> AudioDeviceID? {
  var address = AudioObjectPropertyAddress(mSelector: kAudioHardwarePropertyDevices, mScope: kAudioObjectPropertyScopeGlobal, mElement: kAudioObjectPropertyElementMain)
  var size: UInt32 = 0; guard AudioObjectGetPropertyDataSize(AudioObjectID(kAudioObjectSystemObject), &address, 0, nil, &size) == noErr else { return nil }
  var devices = [AudioDeviceID](repeating: 0, count: Int(size) / MemoryLayout<AudioDeviceID>.size)
  guard AudioObjectGetPropertyData(AudioObjectID(kAudioObjectSystemObject), &address, 0, nil, &size, &devices) == noErr else { return nil }
  return devices.first { device in
    guard deviceName(device) == name else { return false }
    var streamAddress = AudioObjectPropertyAddress(mSelector: kAudioDevicePropertyStreamConfiguration, mScope: input ? kAudioDevicePropertyScopeInput : kAudioDevicePropertyScopeOutput, mElement: kAudioObjectPropertyElementMain)
    var streamSize: UInt32 = 0; return AudioObjectGetPropertyDataSize(device, &streamAddress, 0, nil, &streamSize) == noErr && streamSize > 0
  }
}

func deviceName(_ device: AudioDeviceID) -> String? {
  var address = AudioObjectPropertyAddress(mSelector: kAudioObjectPropertyName, mScope: kAudioObjectPropertyScopeGlobal, mElement: kAudioObjectPropertyElementMain)
  var name: Unmanaged<CFString>?; var size = UInt32(MemoryLayout<Unmanaged<CFString>?>.size)
  guard AudioObjectGetPropertyData(device, &address, 0, nil, &size, &name) == noErr, let name else { return nil }; return name.takeUnretainedValue() as String
}
