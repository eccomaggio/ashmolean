from pathlib import Path
import argparse
import logging
from csv import reader, writer
from openpyxl import load_workbook  # type: ignore[import]



def argument_parser() -> tuple[Path, Path, Path]:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(description="Process some text files.")
    parser.add_argument(
        "-s", "--source", type=Path, default=Path("input.txt"), help="Source file path"
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=Path("output.csv"),
        help="Destination file path",
    )
    parser.add_argument(
        "-c",
        "--concordance",
        type=Path,
        default=Path("concordance.xlsx"),
        help="Concordance file path",
    )
    args = parser.parse_args()
    return (args.source, args.output, args.concordance)


def read_lines(file_path: Path) -> list[str]:
    """Read lines from a file and return them as a list."""
    with file_path.open("r", encoding="utf-8") as file:
        raw_lines = file.readlines()
    # if ord(raw_lines[0][0]) == 65279:
    #     raw_lines[0] = raw_lines[0][1:]
    raw_lines[0] = remove_bom(raw_lines[0])
    raw_lines = [line.strip() for line in raw_lines]
    return raw_lines
    # return file.readlines()


def remove_bom(line: str) -> str:
    # print(f">>> start character={ord(line[0])} ({ord(line[0]) == 65279})")
    if ord(line[0]) == 65279:
        line = line[2:]
        logging.info(f"Removed BOM from start of file. <{line[:10]}>")
        # return line[1:]
    # if line.startswith("\xFF\xFE"):
    #     logging.info("Removed BOM from start of file.")
    #     return line[3:]
    else:
        logging.error("No BOM found.")
    return line


def write_lines(file_path: Path, lines: list[str]) -> None:
    """Write a list of lines to a file."""
    with file_path.open("w", encoding="utf-8") as file:
        file.writelines(lines)


def write_csv(file_path: Path, data: list) -> None:
    """Write a list of lists to a CSV file."""
    with file_path.open("w", encoding="utf-8", newline="") as file:
        csv_writer = writer(file)
        csv_writer.writerows(data)


def read_csv(file_path: Path) -> list[list[str]]:
    """Read a CSV file and return its content as a list of lists."""
    with file_path.open("r", encoding="utf-8") as file:
        csv_reader = reader(file)
        return list(csv_reader)


def extract_from_excel(excel_file_address: Path) -> list[list[str]]:
    """
    excel seems pretty random in how it assigns string/int/float, so...
    this routine coerces everything into a string,
    strips ".0" from misrecognised floats
    & removes trailing spaces
    """
    excel_file_name: str = str(excel_file_address.resolve())
    excel_sheet = load_workbook(filename=excel_file_name).active
    sheet = []
    if excel_sheet:
        for excel_row in excel_sheet.iter_rows(min_row=2, values_only=True):
            row = []
            if not excel_row[0]:
                break
            for col in excel_row:
                if col:
                    data = str(col).strip()
                    data = trim_mistaken_decimals(data)
                else:
                    data = ""
                row.append(data)
            sheet.append(row)
    return sheet


def trim_mistaken_decimals(string: str) -> str:
    if string.endswith(".0"):
        string = string[:-2]
    return string