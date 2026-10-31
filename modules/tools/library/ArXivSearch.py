import httpx

query = args.get('query')
max_results = args.get('max_results', 5)

if not query:
    result = "Error: No query provided."
else:
    try:
        base_url = "http://export.arxiv.org/api/query"
        params = {
            "search_query": f"all:{query}",
            "start": 0,
            "max_results": min(max_results, 20),
            "sortBy": "relevance",
            "sortOrder": "descending"
        }
        
        response = httpx.get(base_url, params=params, timeout=15.0)
        response.raise_for_status()
        
        from xml.etree import ElementTree as ET
        root = ET.fromstring(response.text)
        
        ns = {'atom': 'http://www.w3.org/2005/Atom'}
        entries = root.findall('atom:entry', ns)
        
        if not entries:
            result = f"No papers found for query: '{query}'"
        else:
            papers = []
            for i, entry in enumerate(entries, 1):
                # Safe extraction with None guards
                title_el = entry.find('atom:title', ns)
                title = title_el.text.strip().replace('\n', ' ') if title_el is not None else 'Unknown Title'
                
                summary_el = entry.find('atom:summary', ns)
                summary = summary_el.text.strip().replace('\n', ' ')[:300] if summary_el is not None else 'No summary available'
                
                authors = []
                for a in entry.findall('atom:author', ns)[:3]:
                    name_el = a.find('atom:name', ns)
                    if name_el is not None and name_el.text:
                        authors.append(name_el.text)
                
                published_el = entry.find('atom:published', ns)
                published = published_el.text[:10] if published_el is not None and published_el.text else 'N/A'
                
                pdf_link = None
                for link in entry.findall('atom:link', ns):
                    if link.get('title') == 'pdf':
                        pdf_link = link.get('href')
                        break
                
                papers.append(f"**{i}. {title}**")
                papers.append(f"   Authors: {', '.join(authors) if authors else 'Unknown'}")
                papers.append(f"   Published: {published}")
                papers.append(f"   Summary: {summary}...")
                if pdf_link:
                    papers.append(f"   PDF: {pdf_link}")
                papers.append("")
            
            result = "ArXiv Search Results:\n\n" + "\n".join(papers)
            
    except httpx.HTTPError as e:
        result = f"HTTP error occurred: {str(e)}"
    except Exception as e:
        result = f"Error searching ArXiv: {str(e)}"

