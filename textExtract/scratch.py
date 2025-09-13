import re
from pprint import pprint
from dataclasses import dataclass


# @dataclass
# class Content:
#     pub_date: str
#     processed_sections: dict[str, list[str]]
#     current_lines: list[str]
#     line: str
#     current_section_keys: list[str]


class Content:
    def __init__(self, pd: str, ps: dict, cl: list, line: str, csk: list, ci=False):
        self.pub_date = pd
        self.processed_sections = ps
        self.current_lines = cl
        self.line = line
        self.current_section_keys = csk
        self.currently_ignoring = ci

    def update_processed_sections(self):
        for key in self.current_section_keys:
            self.processed_sections[key] = self.current_lines
        self.current_lines = []
        self.current_section_keys = []

    def start_new_section(self, new_section_keys: list[str]):
        self.update_processed_sections()
        self.current_section_keys = new_section_keys

    def update_current_lines(self):
        self.current_lines.append(self.line)
        self.line = ""


@dataclass
class Command:
    verb: str
    object_list: list[str]


def process(raw_lines: list[str], concordance: dict[str, list[str]]) -> Content:
    """
    This will replace group_lines(), get_command(), get_inline_commands() & parse_inline_command()
    This sorts the lines (paragraphs) into sections
    filters out lines that aren't needed
    and expands any inline commands
    """
    content: Content = Content("", {}, [], "", [])
    for raw_line in raw_lines:
        if not raw_line:
            continue
        phrases = chunk_by_command(raw_line)
        for _type, cmd, other in phrases:
            print(f">>>>>{_type}={cmd.verb}: {cmd.object_list}")
            match _type:
                case "none":
                    content.line += other
                case "V":
                    content = process_verb(cmd, content)
                case "VO":
                    content = process_verb_object(cmd, content, concordance)
                case "unknown":
                    print(f">>>> UNKNOWN command: {cmd}")
        content.update_current_lines()
    if content.current_lines:
        content.update_processed_sections()
        # for key in content.current_section_keys:
        #     content.processed_sections[key] = content.current_lines
        #     content.current_lines = []
    # if pub_date:
    #     logging.info(f"Pub date: {pub_date}")
    # else:
    #     logging.critical("No publication date found!")
    return content


def chunk_by_command(line: str) -> list[tuple[str, Command, str]]:
    open_cmd = False
    output = []
    pre_parsed_line = [el for el in re.split(r"(@@)", line) if el]
    ## re group preserves "@@": allows it to be matched with command to distinguish "PROCESS" (text) vs "@@PROCESS" (cmd)
    for phrase in pre_parsed_line:
        if phrase == "@@":
            open_cmd = not open_cmd
            continue
        if open_cmd:
            _type, cmd, other = parse_command(phrase)
        else:
            _type = "none"
            cmd = Command("", [])
            other = phrase
        output.append((_type, cmd, other))
    return output


def parse_command(phrase: str) -> tuple[str, Command, str]:
    simple_commands = ["process", "ignore"]  # "@@CMD\n" or "@@CMD@@..."
    compound_commands = ["meta", "link", "new"]  # "@@CMD:value@@"
    _type = ""
    cmd = Command("", [])
    other = ""
    if phrase.lower() in simple_commands:
        cmd.verb = phrase.lower()
        _type = "V"
    else:
        if len(tmp := phrase.split(":")) == 1:
            other = phrase
            _type = "unknown"
        else:
            cmd.verb, raw_object = tmp
            cmd.object_list = raw_object.split("=")
            cmd.verb = cmd.verb.lower()
            if cmd.verb in compound_commands:
                _type = "VO"
            else:
                cmd.verb, cmd.object_list, other = "", [], phrase
                _type = "none"

    return (_type, cmd, other)


def process_verb(cmd: Command, content: Content) -> Content:
    match cmd.verb:
        case "ignore":
            content.currently_ignoring = True
        case "process":
            content.currently_ignoring = False
        case "unknown":
            msg = f"unknown verb ({cmd.object_list})"
            print(msg)
    content.line += f"**{cmd.verb}**"
    return content


def process_verb_object(cmd: Command, content: Content, concordance) -> Content:
    match cmd.verb:
        case "link":
            text = cmd.object_list[0]
            # currently, links are ignored, but see this possibility:
            # number = re.sub(r"[^\d]", "", text)
            # if number:
            #     ref = concordance.get(number, ["", ""])[1]
            #     content.line = f" [{ref}]" if ref else ""
            content.line = text
        case "meta":
            if cmd.object_list[0].lower() == "pub_date":
                content.pub_date = cmd.object_list[1]
            else:
                pass
        case "new":
            if (key_count := len(content.current_section_keys)) > 1:
                # overview.count["EXTRA_sections"] += key_count - 1
                print(f"EXTRA_sections {key_count - 1}")
            new_section_keys = [
                el for el in re.split(r"[\s&,]", cmd.object_list[0]) if el
            ]
            content.start_new_section(new_section_keys)
            content.currently_ignoring = True  ## ignore up to "process" in Penny
        case _:
            # logging.warning(f"The command '{command}' is unknown.")
            print(f"The command '{cmd.verb}' is unknown.")
            content.line = str(cmd.object_list)
        # overview.count[command] += 1
    return content


# x = "@@PROCESS"
# x = "@@PROCESS@@"
# x = "@@PROCESS@@And this is a @@LINK:link@@ and more text. See also @@LINK:No. 434@@."
# x = "LINK@@PROCESS@@And this is a @@LINK:link@@ and more text. See also @@LINK:No. 434@@."
# x = "PROCESS@@PROCESS@@And this is a @@LINK:link@@ and more text. See also @@LINK:No. 434@@."
# x = "PROCESS@@PROCESS@@And this is a @@LINK:link@@ but there's a textual colon: see also @@LINK:No. 434@@."
# x = "PROCESS@@PROCESS@@And this is a @@LINK:link@@ with a textual colon: and an unknown command @@HALT:No. 434@@."
x = "PROCESS@@PROCESS@@And this is a @@LINK:link@@ with a textual colon: and an unknown command @@HALT@@."
# x = "@@META:PUB_DATE=01/01/1992"


z = [
    "@@META:pub_date=03/03/1966",
    "@@NEW:1&2",
    "PROCESS@@PROCESS@@a @@LINK:link@@ with textual colon: and unknown command @@HALT:No. 434@@.",
    "This is a line with no commands in it."
]

# y = [el for el in re.split(r"(@@)", x) if el]

for el in z:
    chunks = chunk_by_command(el)
    pprint(chunks)

text = process(z, {})
print(text.pub_date)
pprint(text.processed_sections)
