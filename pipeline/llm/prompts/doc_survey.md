You are cataloguing one of a client's documents so a content writer can use it later. Read the
whole document and return three things.

1. "role": exactly one of
   - "strategy": a marketing, content, growth or positioning plan, audience profiles, keyword research
   - "past_content": pieces the client has already written or published (blog posts, emails, site copy)
   - "brand": a brand book or style guide
   - "notes": meeting notes, call transcripts, interviews
   - "other": anything else

2. "summary": two to four plain sentences on what the document covers and what a writer would
   go to it for. No marketing language.

3. "plan_items": every planned piece of content the document lays out -- an article list, an
   editorial calendar, keyword clusters with example keywords, a numbered content series, an
   appendix of topics. One item per planned piece. Leave the list empty if the document plans no
   content. Do not invent items the document doesn't contain; a keyword cluster with one example
   keyword is one item, titled after what that keyword asks for.
   For each item:
   - "title": a working title for the piece
   - "format": one of blog_post, email, social_post, linkedin_message, landing_page (blog_post if unclear)
   - "priority": its position in the document's order or stated priority, 1 = first; 0 if the
     document gives no order
   - "primary_keyword": the search keyword it targets, if any
   - "cluster": the group or category it belongs to, if any
   - "audience": who it is for, if the document says
   - "notes": the document's stated purpose or angle for it, one sentence, if any
   - "location": where in the document it appears (section heading or page)

Respond as a single JSON object:
{"role": "strategy", "summary": "...", "plan_items": [{"title": "...", "format": "blog_post", "priority": 1, "primary_keyword": "...", "cluster": "...", "audience": "...", "notes": "...", "location": "..."}]}
