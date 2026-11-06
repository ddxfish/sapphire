# Knowledge — the Library

The Library is where reference material lives: notes, files, whole books, folders you keep on disk, and pictures. Sapphire searches it, reads it page by page, and shelves new things herself. It's the knowledge layer of the [Mind Palace](MEMORY.md), at **Mind → Knowledge**.

Memories are short snippets about what happened ([MEMORY.md](MEMORY.md)). The Library is for anything longer you want her to be able to look up.

<img width="50%" alt="sapphire-knowledge" src="https://github.com/user-attachments/assets/405a43e1-b542-4489-b213-6eb82c9226e4" />

---

## How it's organized

**Category → Topic → documents.** Categories and topics are drawers with descriptions, and the descriptions are search surface for her and for you, so write them as you'd explain the shelf to someone. **+ Category** makes a drawer; **+ topic** on a category adds one inside it.

Every document has a kind, filtered by the **All / 📝 Notes / 📄 Files / 🖼️ Images** pills.

## Adding things

| Button | What it does |
|---|---|
| **+ 📝 Note** | Write a note straight into a topic |
| **+ 📄 File** | Upload one text file, PDF or EPUB |
| **+ 📚 Bulk** | Import many files at once, in the background, with live progress chips |
| **+ 🖼️ Images** | Add pictures, searchable by what's in them and by caption |
| **+ 📂 Folder** | **Watch a folder**: index it in place, with the folder as the source of truth. ↻ rescans; a file that leaves the folder shows a **missing** chip |

Long documents are split into sections and embedded for meaning-based search. Importance is set automatically per kind.

## Reading

Open a document for the reader. **⬇ original** downloads the file byte-for-byte, **⬇ text** the extracted text. Pictures open in a lightbox with annotations, and **⬇ annotated** saves them with the marks drawn on.

## Moving a library

**⇓ Export** downloads the whole scope's library as a zip (files and annotations). **⇑ Import** brings a zip into the current scope; re-imports skip what's already there. Watched folders come across as entries to re-add by hand, because a zip never scans your disk.

## Scopes

The Library rides the chat's **memory scope**, like the rest of the Mind (see [Scopes](MEMORY.md#scopes)). There's no separate knowledge scope. A chat on scope `work` sees `work`'s library plus the read-only `global` scope.

## Files attached to one chat

A file you attach in a chat (the **+** by the message box) belongs to that chat only, not the Library. See [Chat documents](CHATS.md#documents). To keep something for good, save it to the Library.

---

## Reference for AI

LIBRARY = Mind Palace knowledge layer (Mind → Knowledge). Category → Topic → documents (notes, files, books, watched folders, images). Same memory_scope as memories; 'global' overlay read-only for you.

TOOLS:
- library() — the catalog: every category, topic and document.
- search_memory(query, layer='knowledge') — search inside documents (mixed searches include knowledge_search_chunks per document, default 2).
- read_document(document_id, page?, around?, start?, end?, private_key?) — [doc N]; page=N reads in order (5 sections a page); around=N = section N with neighbors (follow a search hit); start/end = a range.
- memory_save_image(source, topic, caption?, private_key?) — keep an image (img:<id>, doc:<N>, absolute path, URL) under a topic, searchable by pixels + caption.
- memory_view_image(query? | document_id? | image_id?, count?) — library pictures by what's in them; the user sees them too; matches are relative to the scope's own scores, so 'none look like that' lists the nearest.
- local_view_images(paths | folder, page?, count?) — image files on this computer as numbered tiles (folders paged).
- No save_knowledge/search_knowledge/delete_knowledge: those were the legacy engine's.

Chat attachments are NOT the library: read_attachment/search_attachments (CHATS.md).
