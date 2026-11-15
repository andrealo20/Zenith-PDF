import PDFKit
import PhotosUI
import SwiftUI
import UniformTypeIdentifiers
import VisionKit

/// Photos, scan, PDF pages, blank page.
struct InsertMenu: View {
    private let model: EditorModel
    @State private var picksPhotos = false
    @State private var photosAsPages = false
    @State private var photoItems: [PhotosPickerItem] = []
    @State private var importsPDF = false
    @State private var scans = false

    init(model: EditorModel) {
        self.model = model
    }

    /// Menu: Photo (PhotosPicker), Photo as Page, Scan Document (if VNDocumentCameraViewController.isSupported),
    /// Pages from PDF (fileImporter .pdf), Blank Page; each calls ImageInsertion or the NoteDocument page operations.
    var body: some View {
        Menu {
            Button {
                pickPhotos(asPages: false)
            } label: {
                Label("Photo", systemImage: "photo")
            }
            Button {
                pickPhotos(asPages: true)
            } label: {
                Label("Photo as Page", systemImage: "photo.on.rectangle")
            }
            if VNDocumentCameraViewController.isSupported {
                Button {
                    scans = true
                } label: {
                    Label("Scan Document", systemImage: "doc.viewfinder")
                }
            }
            Button {
                importsPDF = true
            } label: {
                Label("Pages from PDF", systemImage: "doc.on.doc")
            }
            Button {
                model.addPageAfterCurrent()
            } label: {
                Label("Blank Page", systemImage: "doc.badge.plus")
            }
        } label: {
            Label("Insert", systemImage: "plus.rectangle.on.rectangle")
        }
        .disabled(model.document.isReadOnly)
        .accessibilityIdentifier("insertMenu")
        .photosPicker(isPresented: $picksPhotos, selection: $photoItems, maxSelectionCount: photosAsPages ? nil : 1,
                      matching: .images)
        .onChange(of: photoItems) { _, items in
            guard !items.isEmpty else { return }
            photoItems = []
            let asPages = photosAsPages
            Task { await insertPhotos(items, asPages: asPages) }
        }
        .fileImporter(isPresented: $importsPDF, allowedContentTypes: [.pdf], allowsMultipleSelection: false) { result in
            guard case .success(let urls) = result, let url = urls.first else { return }
            Task { await importPages(from: url) }
        }
        .fullScreenCover(isPresented: $scans) {
            DocumentScanner { images in
                scans = false
                insertPages(images)
            }
            .ignoresSafeArea()
        }
    }

    private func pickPhotos(asPages: Bool) {
        photosAsPages = asPages
        picksPhotos = true
    }

    private func insertPhotos(_ items: [PhotosPickerItem], asPages: Bool) async {
        var images: [UIImage] = []
        for item in items {
            if let data = try? await item.loadTransferable(type: Data.self), let image = UIImage(data: data) {
                images.append(image)
            }
        }
        if asPages {
            insertPages(images)
        } else if let image = images.first {
            ImageInsertion.insert(image, into: model.controller)
        }
    }

    /// Pages go after the current one, and the editor shows the first of them.
    private func insertPages(_ images: [UIImage]) {
        guard !images.isEmpty else { return }
        let index = min(model.currentPage + 1, model.document.pages.count)
        model.document.insertImagePages(images, at: index)
        model.controller.go(toPage: index, animated: true)
    }

    private func importPages(from url: URL) async {
        let scoped = url.startAccessingSecurityScopedResource()
        defer { if scoped { url.stopAccessingSecurityScopedResource() } }
        let data = try? await Task.detached(priority: .userInitiated) { try FileStore.read(url) }.value
        guard let data, let other = PDFDocument(data: data) else {
            model.document.lastError = .invalidPDF
            return
        }
        guard !other.isLocked else {
            model.document.lastError = .locked(url)
            return
        }
        let index = min(model.currentPage + 1, model.document.pages.count)
        model.document.importPages(from: other, at: index)
        model.controller.go(toPage: index, animated: true)
    }
}
