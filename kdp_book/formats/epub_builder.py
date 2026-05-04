"""EPUB builder using `ebooklib`."""

from __future__ import annotations

from pathlib import Path

from ebooklib import epub

from kdp_book.log import log
from kdp_book.models.book import BookType, IBookState


def build_epub(state: IBookState, output_path: Path) -> Path:
    if state.manuscript is None or not state.manuscript.chapters:
        raise RuntimeError("Cannot build EPUB: manuscript missing")
    if state.concept is None:
        raise RuntimeError("Cannot build EPUB: concept missing")

    book_dir = Path(state.book_dir)
    book = epub.EpubBook()
    book.set_identifier(f"kdp-book-{state.slug}")
    book.set_title(state.concept.title)
    book.set_language(state.config.language)
    book.add_author(state.config.author)
    if state.concept.subtitle:
        book.add_metadata("DC", "description", state.concept.subtitle)

    is_picture_book = state.config.book_type == BookType.CHILDREN_PICTURE_BOOK

    if state.cover and state.cover.front_image_path:
        front = book_dir / state.cover.front_image_path
        if front.exists():
            book.set_cover("cover.png", front.read_bytes())

    images_by_chapter: dict[int, list[str]] = {}
    for img in state.images:
        images_by_chapter.setdefault(img.chapter_index, []).append(img.image_path)

    chapter_items: list = []
    spine_items: list = ["nav"]

    for chapter in state.manuscript.chapters:
        ch_html = epub.EpubHtml(
            title=chapter.title,
            file_name=f"chap_{chapter.index:02d}.xhtml",
            lang=state.config.language,
        )
        body_parts: list[str] = []
        if not is_picture_book:
            body_parts.append(f"<h1>{_escape(chapter.title)}</h1>")
        for img_path in images_by_chapter.get(chapter.index, [])[:1]:
            full = book_dir / img_path
            if full.exists():
                epub_img_name = f"img_{Path(img_path).name}"
                book.add_item(epub.EpubImage(
                    uid=epub_img_name,
                    file_name=epub_img_name,
                    media_type="image/png",
                    content=full.read_bytes(),
                ))
                img_style = (
                    'style="display:block;margin:0 auto;max-width:100%;height:auto"'
                    if is_picture_book
                    else 'style="text-align:center"'
                )
                if is_picture_book:
                    body_parts.append(
                        f'<p style="text-align:center;margin:0;padding:0">'
                        f'<img src="{epub_img_name}" alt="{_escape(chapter.title)}" {img_style}/></p>'
                    )
                else:
                    body_parts.append(
                        f'<p {img_style}><img src="{epub_img_name}" alt="chapter art"/></p>'
                    )
        # Picture-book pages bake the prose into the artwork itself, so we
        # intentionally skip the prose <p> blocks. For accessibility the alt
        # text on the <img> carries the chapter title.
        if not is_picture_book:
            for para in (chapter.prose or "").split("\n\n"):
                text = para.strip()
                if text:
                    body_parts.append(f"<p>{_escape(text)}</p>")
        ch_html.set_content("".join(body_parts))
        book.add_item(ch_html)
        chapter_items.append(ch_html)
        spine_items.append(ch_html)

    book.toc = tuple(chapter_items)
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    book.spine = spine_items

    output_path.parent.mkdir(parents=True, exist_ok=True)
    epub.write_epub(str(output_path), book)
    log.info("EPUB: %s (%d chapters, picture_book=%s)", output_path, len(chapter_items), is_picture_book)
    return output_path


def _escape(text: str) -> str:
    return (
        text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        .replace("\n", "<br/>")
    )
