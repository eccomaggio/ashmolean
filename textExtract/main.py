from pathlib import Path
from csv import reader, writer
import argparse
# from pprint import pprint
# from openpyxl import load_workbook, Workbook
from openpyxl import load_workbook  # type: ignore[import]
# import datetime
# import pytz
from enum import Enum, auto
from dataclasses import dataclass
# from codecs import BOM_UTF8
import logging

logging.basicConfig(
    filename='textExtract.log',
    level=logging.INFO,
    # format='%(asctime)s %(levelname)s:%(message)s'
    format='%(levelname)s:%(message)s',
    filemode="w",
    encoding="utf-8",
)


class Instruction(Enum):
    NONE = auto()
    NEW_SECTION = auto()
    PROCESS = auto()
    IGNORE = auto()
    META = auto()
    UNKNOWN = auto()


@dataclass
class Command:
    order: Instruction
    details: list[str]


type ExcelRow = tuple[str, str, str, str, str, str, str, str, str, str, str, str, str]

def argument_parser() -> tuple[Path, Path, Path]:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(description="Process some text files.")
    parser.add_argument("-s", "--source", type=Path, default=Path("input.txt"), help="Source file path")
    parser.add_argument("-o", "--output", type=Path, default=Path("output.csv"), help="Destination file path")
    parser.add_argument("-c", "--concordance", type=Path, default=Path("concordance.xlsx"), help="Concordance file path")
    args = parser.parse_args()
    return (args.source, args.output, args.concordance)


def read_lines(file_path: Path) -> list[str]:
    """Read lines from a file and return them as a list."""
    with file_path.open("r", encoding="utf-8") as file:
        raw_lines = file.readlines()
    if ord(raw_lines[0][0]) == 65279:
        raw_lines[0] = raw_lines[0][1:]
    return raw_lines
        # return file.readlines()


def write_lines(file_path: Path, lines: list[str]) -> None:
    """Write a list of lines to a file."""
    with file_path.open("w", encoding="utf-8") as file:
        file.writelines(lines)


def write_csv(file_path: Path, data: list) -> None:
    """Write a list of lists to a CSV file."""
    with file_path.open("w", encoding="utf-8", newline='') as file:
        csv_writer = writer(file)
        csv_writer.writerows(data)


def remove_bom(line: str) -> str:
    # return line[3:] if line.startswith(codecs.BOM_UTF8) else line
    return line[3:] if line.startswith("\xFF\xFE") else line

def read_csv(file_path: Path) -> list[str]:
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


def make_concordance(excel_file_path: str) -> dict[str, list[str]]:
    """
    concordance layout with example data:
    row 0*  1*                   2                           3                   4                       5 (key)
    ObjID	ObjectNumber	    ObjectNumberSorted	        ReferenceNumber	    ObjectTitle	            PW:litCatNo
    742939	WA1947.191.176.1	WA1947.00191.00176.00001	Penny (1992) 1	    Flagellator of Christ	1

    PW:litCatNo (column 5) is the number given in the publication, so it is the key to the object id
    """
    raw = extract_from_excel(Path(excel_file_path))
    concordance = normalise_concordance(raw)
    return concordance


def normalise_concordance(raw: list[list[str]]) -> dict[str, list[str]]:
    concordance: dict[str, list[str]] = {}
    for row in raw:
        object_id = row[0]
        object_num = row[1]
        if row[5].isnumeric():
            concordance[row[5]] = [object_id, object_num]
    return concordance


def get_command(line: str) -> Command:
    order = Instruction.NONE
    detail = []
    if line.startswith("@@"):
        line = line[2:]
        match line:
            case line if line[0].isnumeric():
                order = Instruction.NEW_SECTION
                detail = line.split("&")
            case line if line.startswith("IGNORE"):
                order = Instruction.IGNORE
            case line if line.startswith("PROCESS"):
                order = Instruction.PROCESS
            case line if line.startswith("META:"):
                order = Instruction.META
                detail = line[5:].split("=")
            case _:
                order = Instruction.UNKNOWN
                detail = [line]
        if order.value != Instruction.NONE.value:
            logging.info(Command(order, detail))
    return Command(order, detail)


def group_lines(raw_lines: list[str], concordance: dict[str, list[str]]) -> tuple[dict[str, list[str]], str]:
    """Process the text from the source list and return a list of processed lines."""
    pub_date = ""
    processed_text: dict[str, list[str]] = {}
    section: list[str] = []
    currently_ignoring = False
    current_sections: list[str] = []
    # if ord(raw_lines[0][0]) == 65279:
    #     raw_lines[0] = raw_lines[0][1:]
    for line in raw_lines:
        line = line.strip()
        if not line:
            continue
        command: Command = get_command(line)
        match command.order:
            case Instruction.NONE:
                if currently_ignoring:
                    continue
                else:
                    section.append(line)
            case Instruction.IGNORE:
                currently_ignoring = True
            case Instruction.PROCESS:
                currently_ignoring = False
            case Instruction.NEW_SECTION:
                if len(current_sections) > 1:
                    section.insert(0, create_shared_description_message(current_sections, concordance))
                for section_to_save in current_sections:
                    processed_text[section_to_save] = section
                current_sections = command.details
                currently_ignoring = True
                section = []
            case Instruction.META:
                if command.details[0].lower() == "pub_date":
                    pub_date = command.details[1]
            case Instruction.UNKNOWN:
                msg = "Unknown command used (check spelling?)"
                logging.warning(msg)
                # raise UserWarning("Unknown command used (check spelling?)")
    if section:
        for section_to_save in current_sections:
            processed_text[section_to_save] = section
    return (processed_text, pub_date)


def create_shared_description_message(current_sections: list[str], concordance: dict[str, list[str]]) -> str:
    shared_blurb = " & ". join((f"{section} ({concordance[section][1]})" for section in current_sections))
    message = f"[Description shared between items {shared_blurb}]"
    logging.info(message)
    return message


def prepare_for_csv(processed_text: dict[str, list[str]], concordance: dict[str, list[str]], published_date: str, import_identifier: str,) -> list[ExcelRow]:
    headings = (
        "Object ID", # Museum+ id of the item described
        "Import identifier",    # name given to this batch operation
        "Type", # Always catalogue text
        "Sort", # Always '100' in case of multiple entries
        "Purpose",  # TODO: check this should be empty = 'purpose'
        "Audience", # Always 'public'
        "Status",   # Always '05 Published'
        "Language",
        "Published Date",   # DD/MM/YYYY
        # "Author",  # TODO: check this should be empty = 'author'
        # "Literature ID",    # id of the publication
        # "Exhibition",  # TODO: check this should be empty = 'exhibition'
        "Title_or_ref",  # TODO: check this should be empty = 'title/ref. no.'
        "Text",
        "Source",  # TODO: check this should be empty = 'source'
        "Notes"
        )
    output: list[ExcelRow] = []
    object_id: str
    audience = "public"
    purpose = ""
    # author = ""
    # exhibition = ""
    title = ""
    notes = ""
    source = ""
    status = "05 Published"
    _type = "catalogue text"
    language = "en"
    output.append(headings)
    for num, lines in processed_text.items():
        object_from_concordance = concordance.get(num, None)
        if not object_from_concordance:
            continue
        else:
            object_id = object_from_concordance[0]
        _sort = "100"
        text = "\n\n".join(lines)
        output.append((
            object_id,
            import_identifier,
            _type,
            _sort,
            purpose,
            audience,
            status,
            language,
            published_date,
            # author,
            # literature_id,
            # exhibition,
            title,
            text,
            source,
            notes,
        ))
    return output





def main() -> None:
    text_dir = Path("text_files")
    csv_dir = Path("csv_files")
    if not csv_dir.exists():
        csv_dir.mkdir()
    concordance = make_concordance("penny.concordance.xlsx")
    for source_file in text_dir.glob("*.txt"):
        destination_file = csv_dir / f"{source_file.stem}.csv"
        batch_name = source_file.stem
        # published_date = "01/01/1992"

        logging.info(f"Reading from {source_file.name} and writing to {destination_file.name}...")
        raw_lines: list[str] = read_lines(source_file)
        processed_text, published_date = group_lines(raw_lines, concordance)
        del raw_lines
        csv_ready_text = prepare_for_csv(processed_text, concordance, published_date, batch_name)
        del processed_text

        logging.info(f"Processed {len(csv_ready_text) - 1} sections from {source_file.name}." )
        write_csv(destination_file, csv_ready_text)

if __name__ == "__main__":
    main()
