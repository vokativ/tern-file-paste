ObjC.import('AppKit');

function run() {
    // Reading NSURL objects with this option requests file URLs only, not
    // Finder's TIFF/PNG icon previews or the plain-text display name.
    var classes = $.NSArray.arrayWithObject($.NSURL);
    var options = $.NSDictionary.dictionaryWithObjectForKey(
        $.NSNumber.numberWithBool(true), $.NSPasteboardURLReadingFileURLsOnlyKey
    );
    var urls = $.NSPasteboard.generalPasteboard.readObjectsForClassesOptions(classes, options);
    var paths = [];
    if (!urls || urls.isNil()) {
        return JSON.stringify(paths);
    }
    for (var index = 0; index < urls.count; index++) {
        var url = urls.objectAtIndex(index);
        if (!url.isFileURL) {
            throw new Error('Clipboard reader returned a non-file URL');
        }
        var host = ObjC.unwrap(url.host);
        if (host && host.toLowerCase() !== 'localhost') {
            throw new Error('Clipboard file URL has a nonlocal host');
        }
        if (ObjC.unwrap(url.query) || ObjC.unwrap(url.fragment)) {
            throw new Error('Clipboard file URL contains a query or fragment');
        }
        var path = ObjC.unwrap(url.path);
        if (typeof path !== 'string' || path.length === 0 || path.charAt(0) !== '/' || path.indexOf('//') === 0 || path.indexOf('\u0000') !== -1) {
            throw new Error('Clipboard reader returned an invalid file path');
        }
        paths.push(path);
    }
    return JSON.stringify(paths);
}
