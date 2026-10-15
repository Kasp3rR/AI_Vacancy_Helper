import re
import unicodedata
from html.parser import HTMLParser

from app.schemas import Profile, RankingDetails
from app.services.hh import Vacancy

ALIASES = {"postgres": "postgresql", "js": "javascript", "ts": "typescript", "csharp": "c#"}
EXPERIENCE_MINIMUM = {"noExperience": 0, "between1And3": 1, "between3And6": 3, "moreThan6": 6}


def normalize(value: str) -> str:
    value = unicodedata.normalize("NFKC", value)
    value = value.casefold()
    return " ".join(value.split())


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
    parts = [vacancy.name, vacancy.description or ""]
    if vacancy.snippet:
        for value in vacancy.snippet.values():
            parts.append(value or "")

    html = " ".join(parts).replace("\x00", " ")
    parser = PlainText()
    parser.feed(html)
    return normalize(" ".join(parser.parts))


def contains(text: str, term: str) -> bool:
    pattern = r"(?<![\w+#.])" + re.escape(term) + r"(?![\w+#])"
    return re.search(pattern, text) is not None


def compare_skills(profile: Profile, vacancy: Vacancy) -> tuple[list[str], list[str]]:
    owned = set()
    for value in profile.skills:
        name = skill_name(value)
        if name:
            owned.add(name)

    required = set()
    for skill in vacancy.key_skills or []:
        name = skill_name(skill.name)
        if name:
            required.add(name)

    matched = []
    missing = []
    for name in sorted(required):
        if name in owned:
            matched.append(name)
        else:
            missing.append(name)
    return matched, missing


def title_score(profile: Profile, vacancy: Vacancy) -> float:
    title = normalize(profile.title or "")
    terms = set(re.findall(r"[\w]+(?:[+#]+)?|\.net", title))
    for keyword in profile.keywords:
        keyword = normalize(keyword)
        if keyword:
            terms.add(keyword)
    if not terms:
        return 0.0

    text = vacancy_text(vacancy)
    matches = 0
    for term in terms:
        if contains(text, term):
            matches += 1
    return matches / len(terms)


def experience_score(profile: Profile, vacancy: Vacancy) -> float:
    if profile.experience_years is None or vacancy.experience is None:
        return 0.0

    experience_id = vacancy.experience.id or ""
    minimum = EXPERIENCE_MINIMUM.get(experience_id)
    if minimum is None:
        return 0.0
    if experience_id == "moreThan6":
        if profile.experience_years > 6:
            return 1.0
        return 0.0
    if profile.experience_years >= minimum:
        return 1.0
    return 0.0


def salary_score(profile: Profile, vacancy: Vacancy) -> float:
    if not profile.salary:
        return 1.0
    if vacancy.salary is None:
        return 0.0
    if profile.currency != vacancy.salary.currency:
        return 0.0

    offered = vacancy.salary.upper
    if offered is None:
        offered = vacancy.salary.lower
    if offered is None:
        return 0.0
    return max(0.0, min(1.0, offered / profile.salary))


def region_score(profile: Profile, vacancy: Vacancy) -> float:
    score = 0.0
    if not profile.region or not normalize(profile.region):
        score = 1.0
    if profile.region and vacancy.area:
        if normalize(profile.region) == normalize(vacancy.area.name):
            score = 1.0
        else:
            score = 0.0
    return score


def evaluate(profile: Profile, vacancy: Vacancy) -> RankingDetails:
    matched, missing = compare_skills(profile, vacancy)
    required_count = len(matched) + len(missing)
    skills = 0.0
    if required_count > 0:
        skills = len(matched) / required_count

    title_keywords = title_score(profile, vacancy)
    experience = experience_score(profile, vacancy)
    preferences = (salary_score(profile, vacancy) + region_score(profile, vacancy)) / 2
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


def ranking_sort_key(item: tuple[Vacancy, RankingDetails]) -> tuple[float, float, str]:
    vacancy, ranking = item
    numeric_id: float = float("inf")
    if vacancy.id.isdecimal():
        numeric_id = int(vacancy.id)
    return -ranking.score, numeric_id, vacancy.id


def rank_vacancies(
    profile: Profile, vacancies: list[Vacancy]
) -> list[tuple[Vacancy, RankingDetails]]:
    unique = {}
    for vacancy in vacancies:
        unique[vacancy.id] = vacancy

    results = []
    for vacancy in unique.values():
        ranking = evaluate(profile, vacancy)
        results.append((vacancy, ranking))
    results.sort(key=ranking_sort_key)
    return results
