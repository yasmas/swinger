1. Read these rules files, everytime we start a new session and follow these instructions strictly
2. You should obey the directory structure as follows:
- src: all the source code goes here, including reusable utility scripts and data downloaders (e.g. src/data_sources/ for data download and fetch scripts)
- tmp: any temporary data files, or scripts or code that you need one time only goes here
- reports: when I ask you to generate formatted output for me to examine, like CSV/HTML files, it will go here
- docs: instructions for you and documentation for me.
- data: permanent data files (downloaded price data CSVs, etc.)
3. Any decision we make about the model, algorithm, parameters, etc - please document immediatly into docs/decisions.md. summerize the decision BEFORE starting implementation the decision. Keep decisions.md high level, short and concise -- one or two sentences per decision. Full technical details (schemas, code snippets, data formats) belong in docs/detailed design.md, not in decisions.md.
4. At all times keep docs/session-context.md file that documents for you what were you doing and what are the next steps. Also include on that file info how to use it. So when we start a new context/session you can bring yourself up to date
5. NEVER add --trailer, Co-authored-by, or any other metadata/signatures to git commits. Plain commit message only.
