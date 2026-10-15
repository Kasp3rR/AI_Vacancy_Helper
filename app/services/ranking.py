import re
import unicodedata
from html.parser import HTMLParser

from app.schemas import Profile, RankingDetails
from app.services.hh import Vacancy

ALIASES = {"postgres": "postgresql", "js": "javascript", "ts": "typescript", "csharp": "c#"}
EXPERIENCE_MINIMUM = {"noExperience": 0, "between1And3": 1, "between3And6": 3, "moreThan6": 6}


def normalize(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def skill_name(value: str) -> str:
    name = normalize(value)
    return ALIASES.get(name, name)


class PlainText(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def vacancy_text(vacancy: Vacancy) -> str:
    parser = PlainText()
    parser.feed(
        " ".join(
            [
                vacancy.name,
                vacancy.description or "",
                *[value or "" for value in (vacancy.snippet or {}).values()],
            ]
        ).replace("\x00", " ")
    )
    return normalize(" ".join(parser.parts))


def contains(text: str, term: str) -> bool:
    return re.search(r"(?<![\w+#.])" + re.escape(term) + r"(?![\w+#])", text) is not None


def evaluate(profile: Profile, vacancy: Vacancy) -> RankingDetails:
    owned = {skill_name(value) for value in profile.skills if normalize(value)}
    required = {
        skill_name(skill.name) for skill in vacancy.key_skills or [] if normalize(skill.name)
    }
    matched = sorted(owned & required)
    missing = sorted(required - owned)
    skills = len(matched) / len(required) if required else 0.0
    terms = set(re.findall(r"[\w]+(?:[+#]+)?|\.net", normalize(profile.title or "")))
    terms.update(normalize(value) for value in profile.keywords if normalize(value))
    text = vacancy_text(vacancy)
    title_keywords = sum(contains(text, term) for term in terms) / len(terms) if terms else 0.0
    experience = 0.0
    minimum = EXPERIENCE_MINIMUM.get(vacancy.experience.id or "") if vacancy.experience else None
    if minimum is not None and profile.experience_years is not None:
        experience = float(profile.experience_years >= minimum)
        if vacancy.experience and vacancy.experience.id == "moreThan6":
            experience = float(profile.experience_years > 6)
    salary = 1.0 if not profile.salary else 0.0
    if profile.salary and vacancy.salary and profile.currency == vacancy.salary.currency:
        offered = vacancy.salary.upper if vacancy.salary.upper is not None else vacancy.salary.lower
        if offered is not None:
            salary = max(0.0, min(1.0, offered / profile.salary))
    region = 1.0 if not profile.region or not normalize(profile.region) else 0.0
    if profile.region and vacancy.area:
        region = float(normalize(profile.region) == normalize(vacancy.area.name))
    preferences = (salary + region) / 2
    score = round(60 * skills + 20 * title_keywords + 10 * experience + 10 * preferences, 2)
    return RankingDetails(
        score=score,
        matched_skills=matched,
        missing_skills=missing,
        skills=skills,
        title_keywords=title_keywords,
        experience=experience,
        salary_region=preferences,
    )


def rank_vacancies(
    profile: Profile, vacancies: list[Vacancy]
) -> list[tuple[Vacancy, RankingDetails]]:
    unique = {vacancy.id: vacancy for vacancy in vacancies}
    results = [(vacancy, evaluate(profile, vacancy)) for vacancy in unique.values()]
    return sorted(
        results,
        key=lambda item: (
            -item[1].score,
            int(item[0].id) if item[0].id.isdecimal() else float("inf"),
            item[0].id,
        ),
    )
