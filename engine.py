import re
from collections import defaultdict


class SearchEngine:
    def __init__(self):
        # word -> set of document IDs
        self.index = defaultdict(set)

        # document ID -> document data
        self.documents = {}

    def tokenize(self, text):
        """
        Convert text into searchable words.
        """
        text = text.lower()
        return re.findall(r"\b[a-z0-9#@]+\b", text)

    def add_document(self, document_id, text, document):
        """
        Add a document to the inverted index.
        """
        self.documents[document_id] = document

        tokens = self.tokenize(text)

        for token in tokens:
            self.index[token].add(document_id)

    def search(self, query):
        """
        Search documents using keyword matching.
        Results are ranked by number of matching words.
        """
        query_tokens = self.tokenize(query)

        scores = defaultdict(int)

        for token in query_tokens:
            for document_id in self.index.get(token, set()):
                scores[document_id] += 1

        ranked_results = sorted(
            scores.items(),
            key=lambda item: item[1],
            reverse=True
        )

        return [
            {
                "document": self.documents[document_id],
                "score": score
            }
            for document_id, score in ranked_results
        ]

    def autocomplete(self, prefix):
        """
        Return words from the index beginning with the prefix.
        """
        prefix = prefix.lower()

        suggestions = [
            word
            for word in self.index
            if word.startswith(prefix)
        ]

        return sorted(suggestions)[:10]