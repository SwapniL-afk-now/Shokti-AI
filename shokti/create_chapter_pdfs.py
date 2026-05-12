from pathlib import Path

from pypdf import PdfReader, PdfWriter

ROOT_DIR = Path(__file__).resolve().parents[1]
PDF_DIR = ROOT_DIR / "books"
OUTPUT_DIR = PDF_DIR / "chapters"

CHAPTER_SPLITS = {
    "Bio 1st(Hasan Sir-20)_part1.pdf": [
        ("chapter_02_cell_division_pages_067-087", 97, 117),
    ],
    "Bio 1st(Hasan Sir-20)_part2.pdf": [
        ("chapter_06_bryophyta_and_pteridophyta_pages_198-208", 1, 11),
        ("chapter_08_tissue_and_tissue_system_pages_235-254", 38, 57),
    ],
}


def write_pdf_range(reader, output_path, start_page, end_page):
    writer = PdfWriter()
    for page_index in range(start_page - 1, end_page):
        writer.add_page(reader.pages[page_index])

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("wb") as output_file:
        writer.write(output_file)


def create_chapter_pdfs(pdf_path, chapter_ranges):
    reader = PdfReader(pdf_path)
    output_dir = OUTPUT_DIR / pdf_path.stem
    created_files = []

    print(f"Splitting {pdf_path.name} by chapter...")
    for chapter_name, start_page, end_page in chapter_ranges:
        if start_page < 1 or end_page > len(reader.pages) or start_page > end_page:
            raise RuntimeError(
                f"Invalid page range for {pdf_path.name}: "
                f"{chapter_name} uses pages {start_page}-{end_page}, "
                f"but the PDF has {len(reader.pages)} pages."
            )

        output_path = output_dir / f"{chapter_name}.pdf"
        write_pdf_range(reader, output_path, start_page, end_page)
        created_files.append(output_path)
        print(f"  Created {output_path}")

    return created_files


def main():
    created_files = []

    for pdf_name, chapter_ranges in CHAPTER_SPLITS.items():
        pdf_path = PDF_DIR / pdf_name
        if not pdf_path.exists():
            raise FileNotFoundError(f"Missing source PDF: {pdf_path}")

        created_files.extend(create_chapter_pdfs(pdf_path, chapter_ranges))

    print(f"Created {len(created_files)} chapter PDF(s).")
    print(f"Output directory: {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
