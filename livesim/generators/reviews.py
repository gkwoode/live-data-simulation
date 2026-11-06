"""Product reviews with ratings, free text, sentiment and aspect labels for NLP practice."""

from __future__ import annotations

from ..schema import F
from ..util import DailyPattern, Weighted, iso, make_id
from .base import Generator

ASPECTS = {
    "quality": {
        "positive": [
            "The build quality is excellent",
            "It feels solid and well made",
            "Quality is far better than I expected",
            "Still going strong after weeks of daily use",
        ],
        "negative": [
            "It broke within a week",
            "The quality is really disappointing",
            "Feels cheap and flimsy",
            "Stopped working after a few uses",
        ],
        "neutral": ["Quality is about what you would expect for the price", "Build quality is okay, nothing special"],
    },
    "delivery": {
        "positive": [
            "Arrived a day earlier than promised",
            "Shipping was fast and tracking was accurate",
            "Delivered quickly and well packed",
        ],
        "negative": [
            "Delivery took almost two weeks",
            "The package arrived damaged",
            "It was marked as delivered but turned up three days later",
        ],
        "neutral": ["Delivery was on time", "Shipping took the usual few days"],
    },
    "price": {
        "positive": ["Great value for money", "Worth every penny", "Cheaper than in the shops and just as good"],
        "negative": ["Way overpriced for what you get", "Not worth the money", "You can find better for half the price"],
        "neutral": ["Fairly priced", "The price is reasonable but not a bargain"],
    },
    "service": {
        "positive": ["Customer support answered my question within minutes", "The support team was friendly and helpful"],
        "negative": [
            "Customer service never replied to my emails",
            "Support was rude and unhelpful",
            "Getting a refund was a nightmare",
        ],
        "neutral": ["I had to contact support once and they resolved it eventually"],
    },
    "usability": {
        "positive": ["Very easy to set up and use", "Simple and intuitive from the first minute"],
        "negative": ["The instructions are confusing", "Setup was a frustrating experience", "Awkward to use every day"],
        "neutral": ["It took a little while to figure out"],
    },
    "design": {
        "positive": ["It looks great", "Love the design and the colour", "Looks even better in person"],
        "negative": ["Looks nothing like the photos", "The colour is completely different from the listing"],
        "neutral": ["Looks fine, nothing fancy"],
    },
}
CATEGORY_ASPECTS = {
    "Electronics": {
        "battery": {
            "positive": ["Battery easily lasts all day", "Charges quickly and holds the charge"],
            "negative": ["Battery drains far too fast", "It will not hold a charge any more"],
            "neutral": ["Battery life is average"],
        },
        "performance": {
            "positive": ["Fast and responsive", "Performance is excellent even under heavy use"],
            "negative": ["Laggy and slow", "Keeps freezing and needs restarting"],
            "neutral": ["Performance is fine for everyday tasks"],
        },
    },
    "Fashion": {
        "fit": {
            "positive": ["Fits perfectly and true to size", "Comfortable to wear all day"],
            "negative": ["Runs very small, order a size up", "The fit is awful and the seams are crooked"],
            "neutral": ["Fits as expected"],
        }
    },
    "Grocery": {
        "taste": {
            "positive": ["Tastes amazing", "Fresh and full of flavour"],
            "negative": ["Arrived stale", "Tastes artificial and bland"],
            "neutral": ["Tastes okay"],
        }
    },
    "Beauty & Personal Care": {
        "results": {
            "positive": ["My skin feels great after using it", "Noticeable results within a couple of weeks"],
            "negative": ["Gave me a rash", "Did absolutely nothing for me"],
            "neutral": ["Results are subtle so far"],
        }
    },
}
OPENERS = {
    "positive": ["Absolutely love it!", "Highly recommend.", "Really happy with this purchase.", "Exactly what I needed."],
    "negative": ["Very disappointed.", "Do not buy.", "Total waste of money.", "Not happy at all."],
    "neutral": ["It's okay.", "Mixed feelings about this one.", "Decent, but not perfect."],
}
CLOSERS = {
    "positive": ["Would buy again.", "Already ordered another one as a gift.", "Five stars from me."],
    "negative": ["Returning it.", "Never ordering this brand again.", "One star is generous."],
    "neutral": ["Might buy again if it goes on sale.", "Does the job."],
}
TITLES = {
    "positive": ["Love it", "Excellent {noun}", "Great value", "Highly recommended", "Better than expected"],
    "negative": ["Disappointed", "Poor quality {noun}", "Avoid", "Not as described", "Broke quickly"],
    "neutral": ["It's fine", "Average {noun}", "Okay for the price", "Mixed feelings"],
}
FLIP = {"positive": "negative", "negative": "positive", "neutral": "neutral"}


class ReviewsGenerator(Generator):
    name = "reviews"
    title = "Product reviews (NLP)"
    category = "Customer experience"
    description = (
        "Customer reviews with a star rating, title and free-text body built from aspect phrases (quality, delivery, price, "
        "service, battery, fit, taste...). Labels give the text's true sentiment and the aspects mentioned; a few reviews "
        "deliberately disagree with their star rating."
    )
    default_rate = 0.3
    history_per_day = 3000
    key_fields = ("review_id",)
    pattern = DailyPattern([(13.0, 2.0, 0.6), (21.0, 2.0, 1.0)], base=0.3)
    metric = ("Average rating", "rating", "avg")
    related = ("dim_product", "dim_customer", "dim_date")
    fields = [
        F("review_id", "string", "id"),
        F("event_time", "timestamp", "time"),
        F("product_id", "string", "fk", "-> dim_product"),
        F("customer_id", "string", "fk", "-> dim_customer"),
        F("rating", "int", "measure", "1-5 stars"),
        F("title", "string", "text"),
        F("review_text", "string", "text"),
        F("sentiment", "string", "label", "Ground-truth sentiment of the text: positive | neutral | negative"),
        F("aspects", "string", "label", "Comma-separated aspects mentioned in the text"),
        F("verified_purchase", "bool", "attribute"),
        F("helpful_votes", "int", "measure"),
        F("language", "string", "attribute"),
        F("channel", "string", "dimension", "web | mobile_app"),
    ]
    use_cases = {
        "analyst": [
            "Track average rating and review volume by category and brand (join dim_product).",
            "Find products whose ratings are dropping week over week.",
            "Build a word cloud or top-phrases view for negative reviews.",
        ],
        "engineer": [
            "Stream reviews through an enrichment step (language, word count, sentiment model) into a serving table.",
            "Deduplicate repeated submissions and validate text fields.",
            "Index review text in a search engine and keep it in sync with the stream.",
        ],
        "scientist": [
            "Train a sentiment classifier on review_text and compare it with the star rating.",
            "Run aspect-based sentiment analysis using the aspects label.",
            "Topic-model negative reviews (LDA / BERTopic).",
        ],
    }

    def __init__(self, ref, rng):
        super().__init__(ref, rng)
        self.products = Weighted(ref.products, ref.product_weights)
        self.customers = Weighted(ref.customers, ref.customer_weights)

    def _sentence(self, aspect_bank, aspect, sentiment):
        return self.rng.choice(aspect_bank[aspect][sentiment])

    def generate(self, ts):
        rng = self.rng
        product = self.products.pick(rng)
        quality = self.ref.product_quality[product["product_id"]]
        mean = 1 + 4 * quality**0.8
        rating = int(min(5, max(1, round(rng.gauss(mean, 0.9)))))
        sentiment = "positive" if rating >= 4 else "negative" if rating <= 2 else "neutral"
        if rng.random() < 0.05:
            sentiment = FLIP[sentiment]  # sarcasm or a mis-clicked star rating: text disagrees with stars

        bank = dict(ASPECTS)
        bank.update(CATEGORY_ASPECTS.get(product["category"], {}))
        aspects = rng.sample(sorted(bank), rng.choice((1, 2, 2, 3)))
        sentences = []
        if sentiment == "neutral" and len(aspects) >= 2:
            tones = ["positive", "negative"] + ["neutral"] * (len(aspects) - 2)
            rng.shuffle(tones)
        else:
            tones = [sentiment if rng.random() < 0.85 else "neutral" for _ in aspects]
        if rng.random() < 0.6:
            sentences.append(rng.choice(OPENERS[sentiment]))
        for aspect, tone in zip(aspects, tones, strict=True):
            sentences.append(self._sentence(bank, aspect, tone) + ".")
        if rng.random() < 0.5:
            sentences.append(rng.choice(CLOSERS[sentiment]))
        text = " ".join(sentences)
        if rng.random() < 0.08:
            text = text.lower().replace(".", "")  # quick, careless reviews

        noun = self.ref.product_noun[product["product_id"]].lower()
        return [
            {
                "review_id": make_id("REV", ts, rng),
                "event_time": iso(ts),
                "product_id": product["product_id"],
                "customer_id": self.customers.pick(rng)["customer_id"],
                "rating": rating,
                "title": rng.choice(TITLES[sentiment]).format(noun=noun),
                "review_text": text,
                "sentiment": sentiment,
                "aspects": ",".join(aspects),
                "verified_purchase": rng.random() < 0.82,
                "helpful_votes": int(rng.expovariate(0.3)) if rng.random() < 0.6 else 0,
                "language": "en",
                "channel": "mobile_app" if rng.random() < 0.55 else "web",
            }
        ]
