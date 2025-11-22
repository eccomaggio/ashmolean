from difflib import HtmlDiff

with open ("penny_3_a.txt", "r") as orig_file, open("penny_3_b.txt", "r") as changed_file:
    text_a = orig_file.read()
    text_b = changed_file.read()

# with open("penny_3_b.txt", "r") as changed_file:
#     b = changed_file.read()


d = HtmlDiff()
html_diff = d.make_file(text_a.splitlines(), text_b.splitlines())  # a,b were defined earlier
with open("diff.html", "w", encoding="utf-8") as f:
    f.write(html_diff)
