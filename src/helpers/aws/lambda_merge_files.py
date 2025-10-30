import glob
from collections import defaultdict
from datetime import datetime
import os
import argparse

class MarkdownTableMerger:
    """
    A class to find, merge, and sort tables from multiple markdown files.
    """
    def __init__(self, file_prefix: str, output_dir: str = 'extracted/worker'):
        """
        Initializes the MarkdownTableMerger.

        Args:
            file_prefix (str): The prefix of the markdown files to merge (e.g., 'Lambda-Performance-').
            output_dir (str): The directory where the files are located and the output will be saved.
        """
        self.output_dir = output_dir
        self.file_pattern = os.path.join(output_dir, f"{file_prefix}*.md")
        self.output_file = os.path.join(output_dir, f"{file_prefix.strip('-')}.md")
        self.merged_data = defaultdict(list)

    def _parse_markdown_tables(self, file_path: str):
        """Parses a markdown file and extracts tables preceded by H2 headings."""
        tables = defaultdict(list)
        current_function = None
        header = None
        
        with open(file_path, 'r') as f:
            for line in f:
                line = line.strip()
                if line.startswith('## '):
                    current_function = line[3:].strip()
                    header = None  # Reset header for new function
                elif line.startswith('|') and current_function:
                    if header is None:
                        header = [h.strip() for h in line.split('|')[1:-1]]
                    # Check if it's a data row (and not the separator)
                    elif '---' not in line:
                        row_data = [d.strip() for d in line.split('|')[1:-1]]
                        if len(row_data) == len(header):
                            row_dict = dict(zip(header, row_data))
                            tables[current_function].append(row_dict)
        return tables

    def _merge_and_sort_data(self):
        """Merges and sorts data for each function by month."""
        sorted_data = defaultdict(list)
        for function_name, rows in self.merged_data.items():
            # Sort rows by the 'Month' column in descending order
            # The key converts 'MMM-YY' string to a datetime object for correct sorting
            sorted_data[function_name] = sorted(
                rows,
                key=lambda x: datetime.strptime(x['Month'], '%b-%y'),
                reverse=True
            )
        return sorted_data

    def _generate_merged_markdown(self, sorted_data: dict):
        """Generates a single markdown file from the merged and sorted data."""
        with open(self.output_file, 'w') as f:
            report_title = self.output_file.split('/')[-1].replace('.md', '').replace('-', ' ')
            f.write(f"# {report_title}\n\n")
            
            for function_name, rows in sorted(sorted_data.items()):
                if not rows:
                    continue
                
                f.write(f"## {function_name}\n\n")
                
                # Write header
                header = list(rows[0].keys())
                f.write(f"| {' | '.join(header)} |\n")
                f.write(f"|{'|'.join(['---'] * len(header))}|\n")
                
                # Write data rows
                for row in rows:
                    f.write(f"| {' | '.join(row.values())} |\n")
                
                f.write("\n")

    def merge_files(self):
        """Main method to find, parse, merge, and write the markdown reports."""
        files_to_merge = glob.glob(self.file_pattern)
        
        if not files_to_merge:
            print(f"No files found with pattern: '{self.file_pattern}'")
            return
            
        print(f"Found {len(files_to_merge)} files to merge:")
        for f in files_to_merge:
            print(f"- {f}")

        for file_path in files_to_merge:
            tables = self._parse_markdown_tables(file_path)
            for function_name, rows in tables.items():
                self.merged_data[function_name].extend(rows)
                
        sorted_data = self._merge_and_sort_data()
        
        self._generate_merged_markdown(sorted_data)
        
        print(f"\nSuccessfully merged data into '{self.output_file}'")


if __name__ == "__main__":
    file_prefix = None
    output_dir = None

    import sys
    
    # Parse command line arguments
    if len(sys.argv) >= 2:
        file_prefix = sys.argv[1]
    if len(sys.argv) >= 3:
        output_dir = sys.argv[2]

    merger = MarkdownTableMerger(file_prefix=file_prefix, output_dir=output_dir)
    merger.merge_files()
