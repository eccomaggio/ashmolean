from pathlib import Path
import argparse
import logging



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