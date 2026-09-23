# Several facts or narrative sections in a PDF

Use the same captured Scrapy response and `open_pdf` path as a table spider.
Search locally for the project's exact section labels and inspect relevant
pages/footnotes. `pdf_pages` matches all supplied markers on a page; select
separate sections separately when their markers occur on different pages.
Work with page text and reviewed source-specific parsing. Do not force narrative
facts into `pdf_table_records` or build a whole-document regex parser.

A company-level aggregate can be the intended fact. The project contract, not a
universal property-roster rule, decides whether aggregates are useful. Preserve
the fact kind, reporting period, currency/unit and qualifications. Never confuse
an authorization with activity, an annual amount with a quarter, or a component
with its parent total. Missing values remain missing.

Use independent explicit keys for independent facts, even when all cite the
same document URL. Keep the evidence page/section in project-defined features
or extend the domain schema deliberately. The test
`test_two_facts_in_one_document_survive_publication` in
`tests/test_project_contract.py` protects this identity boundary.

A source fixture must cover the actual narrative layout and misleading nearby
values. Test a negative case for a wrong period/section/unit. Budget the model's
inspection, not the runtime's completeness; local text search can index the full
PDF without loading the whole report into model context.
