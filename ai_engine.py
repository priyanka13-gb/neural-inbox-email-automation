import anthropic
import json
import os
import re
import aiosqlite
import math
from typing import Optional

from utils.logger import logger

DB_PATH = os.getenv("DB_PATH", "./neuralinbox.db")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")


class SimpleVectorStore:
    def __init__(self):
        self.documents = []

    def _tokenize(self, text):
        return re.findall(r'\b\w+\b', text.lower())

    def _compute_tfidf(self, tokens):
        tf = {}
        for token in tokens:
            tf[token] = tf.get(token, 0) + 1
        total = len(tokens)
        return {k: v / total for k, v in tf.items()}

    def _cosine_similarity(self, vec1, vec2):
        keys = set(vec1) & set(vec2)
        if not keys:
            return 0.0
        dot = sum(vec1[k] * vec2[k] for k in keys)
        mag1 = math.sqrt(sum(v**2 for v in vec1.values()))
        mag2 = math.sqrt(sum(v**2 for v in vec2.values()))
        if mag1 == 0 or mag2 == 0:
            return 0.0
        return dot / (mag1 * mag2)

    def add_document(self, doc_id, text, metadata):
        tokens = self._tokenize(text)
        tfidf = self._compute_tfidf(tokens)
        self.documents.append({"id": doc_id, "text": text[:500], "metadata": metadata, "tfidf": tfidf})

    def search(self, query, top_k=3):
        if not self.documents:
            return []
        query_tokens = self._tokenize(query)
        query_tfidf = self._compute_tfidf(query_tokens)
        scores = []
        for i, doc in enumerate(self.documents):
            score = self._cosine_similarity(query_tfidf, doc["tfidf"])
            scores.append((score, i))
        scores.sort(reverse=True)
        results = []
        for score, idx in scores[:top_k]:
            if score > 0:
                results.append({**self.documents[idx], "score": score})
        return results


vector_store = SimpleVectorStore()


class AIEngine:
    def __init__(self):
        self.client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    async def load_knowledge_base(self):
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM knowledge_docs LIMIT 20") as cursor:
                docs = await cursor.fetchall()
        if not docs:
            return "No company knowledge base documents uploaded yet."
        context_parts = []
        for doc in docs:
            context_parts.append(f"[{doc['name']}]: {doc['content'][:800]}")
        return "\n\n".join(context_parts)

    async def get_departments(self):
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM departments") as cursor:
                return [dict(row) for row in await cursor.fetchall()]

    async def process_email(self, email_id, sender_name, sender_email, subject, body):
        knowledge_context = await self.load_knowledge_base()
        departments = await self.get_departments()
        dept_list = ", ".join([f"{d['name']} ({d['description']})" for d in departments])

        classification_prompt = f"""You are an email classification AI for a company.

Analyze this incoming email and respond ONLY with a JSON object (no markdown, no explanation):

Email:
From: {sender_name} <{sender_email}>
Subject: {subject}
Body: {body[:1500]}

Departments available: {dept_list}

Respond with this exact JSON structure:
{{
  "intent": "one of: support_request, billing_question, refund_request, order_status, complaint, partnership_inquiry, general_inquiry, spam, urgent_issue",
  "department": "exact department name from the list",
  "sentiment": "one of: positive, neutral, frustrated, angry, confused, excited",
  "urgency": "one of: low, normal, high, critical",
  "summary": "one sentence summary of what this email is about",
  "key_topics": ["topic1", "topic2"]
}}"""

        classification_response = self.client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=500,
            messages=[{"role": "user", "content": classification_prompt}]
        )

        classification_text = classification_response.content[0].text.strip()
        classification_text = re.sub(r'```json|```', '', classification_text).strip()

        try:
            classification = json.loads(classification_text)
        except json.JSONDecodeError:
            classification = {
                "intent": "general_inquiry",
                "department": "General",
                "sentiment": "neutral",
                "urgency": "normal",
                "summary": subject,
                "key_topics": []
            }

        dept_obj = next((d for d in departments if d["name"].lower() == classification["department"].lower()),
                        departments[-1] if departments else None)
        dept_tone = dept_obj["tone"] if dept_obj else "professional"
        dept_signature = dept_obj["signature"] if dept_obj else "Best regards,\nSupport Team"
        auto_send_threshold = dept_obj.get("auto_send_threshold", 0.95) if dept_obj else 0.95

        search_query = f"{subject} {classification['intent']} {' '.join(classification.get('key_topics', []))}"
        rag_results = vector_store.search(search_query, top_k=3)
        rag_context = ""
        if rag_results:
            rag_context = "\n\n".join([f"Source: {r['metadata'].get('name','doc')}\n{r['text']}" for r in rag_results])
        else:
            rag_context = knowledge_context[:2000]

        reply_prompt = f"""You are an email reply AI for a company. Write a helpful, accurate reply.

COMPANY KNOWLEDGE BASE (use this to answer factually — do not make up information):
{rag_context if rag_context else "No specific knowledge available. Reply professionally and offer to help."}

INCOMING EMAIL:
From: {sender_name or 'Customer'} <{sender_email}>
Subject: {subject}
Message: {body[:2000]}

CLASSIFICATION:
- Intent: {classification['intent']}
- Department: {classification['department']}
- Sentiment: {classification['sentiment']}
- Urgency: {classification['urgency']}

INSTRUCTIONS:
- Reply tone: {dept_tone}
- If sender is frustrated/angry: be especially empathetic and apologetic first
- If sentiment is excited/positive: match their energy warmly
- Keep reply concise (3-5 paragraphs max)
- Only state facts found in the knowledge base. If unsure, say you'll follow up
- End with this signature: {dept_signature}
- Do NOT include a subject line in your reply, just the body

Write the reply now:"""

        reply_response = self.client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=800,
            messages=[{"role": "user", "content": reply_prompt}]
        )

        draft = reply_response.content[0].text.strip()

        confidence = self._calculate_confidence(
            intent=classification["intent"],
            sentiment=classification["sentiment"],
            urgency=classification["urgency"],
            has_knowledge=bool(rag_results),
            body_length=len(body)
        )

        logger.info(f"AI Result: {classification['intent']} | {classification['department']} | {confidence:.0%}")

        return {
            "intent": classification["intent"],
            "department": classification["department"],
            "sentiment": classification["sentiment"],
            "urgency": classification["urgency"],
            "summary": classification.get("summary", subject),
            "draft": draft,
            "reasoning": f"Classified as {classification['intent']} for {classification['department']}. Sentiment: {classification['sentiment']}. {'Used knowledge base.' if rag_results else 'No specific KB match found.'}",
            "confidence": confidence,
            "auto_send_threshold": auto_send_threshold
        }

    def _calculate_confidence(self, intent, sentiment, urgency, has_knowledge, body_length):
        score = 0.7
        high_confidence_intents = ["general_inquiry", "order_status", "billing_question"]
        low_confidence_intents = ["complaint", "urgent_issue", "refund_request"]
        if intent in high_confidence_intents:
            score += 0.15
        elif intent in low_confidence_intents:
            score -= 0.2
        if sentiment in ["angry", "frustrated"]:
            score -= 0.15
        elif sentiment == "positive":
            score += 0.05
        if urgency == "critical":
            score -= 0.3
        elif urgency == "high":
            score -= 0.1
        if has_knowledge:
            score += 0.1
        if body_length < 50:
            score -= 0.1
        return max(0.1, min(0.99, score))

    async def regenerate_draft(self, email_id, custom_instructions=None):
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM emails WHERE id = ?", (email_id,)) as cursor:
                email_row = await cursor.fetchone()
        if not email_row:
            raise ValueError("Email not found")
        knowledge_context = await self.load_knowledge_base()
        extra = f"\nExtra instructions from human: {custom_instructions}" if custom_instructions else ""
        prompt = f"""Regenerate a reply for this email.

KNOWLEDGE BASE:
{knowledge_context[:2000]}

EMAIL:
From: {email_row['sender_name']} <{email_row['sender_email']}>
Subject: {email_row['subject']}
Body: {email_row['body_text'][:2000]}

Context: Intent={email_row['intent']}, Sentiment={email_row['sentiment']}, Department={email_row['department']}
{extra}

Write an improved reply:"""

        response = self.client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=800,
            messages=[{"role": "user", "content": prompt}]
        )
        return response.content[0].text.strip()
