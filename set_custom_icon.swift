import AppKit

guard CommandLine.arguments.count == 3 else {
    fputs("Usage: set_custom_icon.swift <icon-png> <target-path>\n", stderr)
    exit(1)
}

let iconPath = CommandLine.arguments[1]
let targetPath = CommandLine.arguments[2]

guard let image = NSImage(contentsOfFile: iconPath) else {
    fputs("Failed to load icon image at \(iconPath)\n", stderr)
    exit(1)
}

let ok = NSWorkspace.shared.setIcon(image, forFile: targetPath, options: [])
if !ok {
    fputs("Failed to assign icon to \(targetPath)\n", stderr)
    exit(1)
}
