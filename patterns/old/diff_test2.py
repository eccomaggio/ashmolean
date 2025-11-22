import difflib
from typing import List

# The simplest, most stable way is to inherit from HtmlDiff and inject
# the necessary CSS to hide the columns corresponding to File A.


class SingleColumnHtmlDiff(difflib.HtmlDiff):
    """
    Generates a difference report using difflib.HtmlDiff, but hides the
    columns for the first file (File A) using CSS injection.

    This provides a single-column view of the modified file (File B)
    with highlighting intact.
    """

    def make_file(
        self,
        a: List[str],
        b: List[str],
        fromdesc: str = "",
        todesc: str = "",
        context: bool = False,
        numlines: int = 5,
    ) -> str:
        """
        Generates the standard HTML diff and injects CSS to force single-column layout.
        """

        # 1. Generate the standard two-column HTML using the parent method
        # This relies on the stable, standard output of difflib.HtmlDiff
        html = super().make_file(a, b, fromdesc, todesc, context, numlines)

        # 2. Define the CSS injection to hide the first three columns (File A + Separator)
        # Note: The table has 5 columns: [A_Num, A_Content, Separator, B_Num, B_Content]

        # The '!' important ensures this override takes precedence over the default inline styles
        SINGLE_COLUMN_CSS_INJECTION = """
        /* Hide Line Number A (1st column), Content A (2nd column), and Separator (3rd column) */
        /*.diff th:nth-child(1),
        .diff th:nth-child(2),
        .diff th:nth-child(3),*/
        tr>th,
        .diff td:nth-child(1),
        .diff td:nth-child(2),
        .diff td:nth-child(3) {
            display: none !important;
        }

        /* Optional: Adjust the table header to only span the two remaining columns */
        .diff thead th {
            /* The parent creates a colspan=5 header. We override the width here
               and rely on the hidden TDs to collapse the table width. */
            width: auto;
            text-align: left;
        }

        /* Ensure the remaining line number column (4th) looks correct */
        .diff td:nth-child(4) {
             padding-right: 10px;
             border-right: 1px solid #ccc;
        }
        """

        # 3. Inject the custom CSS into the existing <style> block
        # The parent method returns the full HTML document. We find the closing
        # </style> tag and insert our custom rules before it.
        if "</style>" in html:
            html = html.replace("</style>", SINGLE_COLUMN_CSS_INJECTION + "\n</style>")

        # 4. Correct the header description (optional aesthetic change)
        # The original header will say 'File A vs File B'. We change it to just 'File B'
        # if todesc and fromdesc:
        #     html = html.replace(
        #         f"Differences ({fromdesc} vs {todesc})", f"Changes in {todesc}"
        #     )

        return html


# --- Example Usage ---

with open("penny_3_a.txt", "r") as orig_file, open(
    "penny_3_b.txt", "r"
) as changed_file:
    text_a = orig_file.read()
    text_b = changed_file.read()

# 1. Instantiate the custom CSS injector class
diff_generator = SingleColumnHtmlDiff()

# 2. Generate the single-column HTML file
result_html = diff_generator.make_file(
    text_a.splitlines(),
    text_b.splitlines(),
    fromdesc="Original Code v1",
    todesc="Refactored Code v2",
    context=True,  # Use grouped context view for a cleaner output
)

# 3. Save the result to a file
output_filepath = "final_single_column_diff.html"
with open(output_filepath, "w", encoding="utf-8") as f:
    f.write(result_html)

print(f"Successfully generated the robust single-column diff file: {output_filepath}")
