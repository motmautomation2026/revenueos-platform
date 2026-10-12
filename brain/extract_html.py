from bs4 import BeautifulSoup

html_path = "data/anvixa.html"
output_path = "data/extracted.txt"

with open(html_path, "r", encoding="utf-8") as file:
    html = file.read()

soup = BeautifulSoup(html, "html.parser")

for tag in soup(["script", "style", "title"]):
    tag.decompose()

for table in soup.find_all("table"):
    rows = []
    for tr in table.find_all("tr"):
        cells = [cell.get_text(" ", strip=True) for cell in tr.find_all(["th", "td"])]
        rows.append(" | ".join(cells))
    table.replace_with("\n" + "\n".join(rows) + "\n")

text = soup.get_text(separator="\n", strip=True)

with open(output_path, "w", encoding="utf-8") as file:
    file.write(text)

print("Characters extracted:", len(text))
print("Saved to:", output_path)