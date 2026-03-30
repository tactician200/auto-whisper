import AppKit

let outputPath = CommandLine.arguments.count > 1 ? CommandLine.arguments[1] : "/tmp/meetingsintel-icon.png"
let size = NSSize(width: 1024, height: 1024)

func color(_ hex: UInt32, alpha: CGFloat = 1.0) -> NSColor {
    NSColor(
        calibratedRed: CGFloat((hex >> 16) & 0xff) / 255.0,
        green: CGFloat((hex >> 8) & 0xff) / 255.0,
        blue: CGFloat(hex & 0xff) / 255.0,
        alpha: alpha
    )
}

let image = NSImage(size: size)
image.lockFocus()

NSGraphicsContext.current?.imageInterpolation = .high

let bgRect = NSRect(x: 64, y: 64, width: 896, height: 896)
let bg = NSBezierPath(roundedRect: bgRect, xRadius: 224, yRadius: 224)
color(0x0f172a).setFill()
bg.fill()

let cardRect = NSRect(x: 180, y: 220, width: 664, height: 540)
let card = NSBezierPath(roundedRect: cardRect, xRadius: 88, yRadius: 88)
color(0x14b8a6).setFill()
card.fill()

let shadowRect = NSRect(x: 180, y: 272, width: 664, height: 432)
let shadow = NSBezierPath(roundedRect: shadowRect, xRadius: 80, yRadius: 80)
color(0x0b1220, alpha: 0.26).setFill()
shadow.fill()

let noteRect = NSRect(x: 286, y: 332, width: 452, height: 332)
let note = NSBezierPath(roundedRect: noteRect, xRadius: 48, yRadius: 48)
color(0xf8fafc).setFill()
note.fill()

let barSpecs: [(CGFloat, CGFloat, CGFloat)] = [
    (342, 404, 184),
    (442, 454, 84),
    (542, 382, 228),
    (642, 438, 116),
]
for (x, y, h) in barSpecs {
    let bar = NSBezierPath(roundedRect: NSRect(x: x, y: y, width: 54, height: h), xRadius: 27, yRadius: 27)
    color(0x0f172a).setFill()
    bar.fill()
}

let curve = NSBezierPath()
curve.lineWidth = 40
curve.lineCapStyle = .round
curve.move(to: NSPoint(x: 322, y: 748))
curve.curve(to: NSPoint(x: 814, y: 650),
            controlPoint1: NSPoint(x: 468, y: 836),
            controlPoint2: NSPoint(x: 676, y: 818))
color(0xf8fafc).setStroke()
curve.stroke()

let arrow = NSBezierPath()
arrow.lineWidth = 34
arrow.lineCapStyle = .round
arrow.lineJoinStyle = .round
arrow.move(to: NSPoint(x: 733, y: 596))
arrow.line(to: NSPoint(x: 848, y: 628))
arrow.line(to: NSPoint(x: 776, y: 725))
color(0xf8fafc).setStroke()
arrow.stroke()

let sparkle = NSBezierPath(ovalIn: NSRect(x: 724, y: 226, width: 84, height: 84))
color(0xf8fafc).setFill()
sparkle.fill()

let plus = NSBezierPath()
plus.lineWidth = 26
plus.lineCapStyle = .round
plus.move(to: NSPoint(x: 766, y: 208))
plus.line(to: NSPoint(x: 766, y: 344))
plus.move(to: NSPoint(x: 698, y: 276))
plus.line(to: NSPoint(x: 834, y: 276))
color(0x0f172a).setStroke()
plus.stroke()

image.unlockFocus()

guard
    let tiff = image.tiffRepresentation,
    let rep = NSBitmapImageRep(data: tiff),
    let png = rep.representation(using: .png, properties: [:])
else {
    fputs("Failed to encode PNG\n", stderr)
    exit(1)
}

do {
    try png.write(to: URL(fileURLWithPath: outputPath))
} catch {
    fputs("Failed to write PNG: \(error)\n", stderr)
    exit(1)
}
