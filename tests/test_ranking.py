import pytest

from app.schemas import Profile
from app.services.hh import Vacancy
from app.services.ranking import evaluate, rank_vacancies
from tests.test_hh import vacancy


def profile(**changes):
    return Profile.model_validate(
        {
            "title": None,
            "skills": [],
            "keywords": [],
            "experience_years": None,
            "salary": 100000,
            "currency": "RUR",
            "region": "Москва",
            **changes,
        }
    )


def card(**changes):
    return Vacancy.model_validate({**vacancy(), "key_skills": [], **changes})


def test_exact_weighted_score_and_explanation():
    result = evaluate(
        profile(title="Python Developer", skills=["python"], experience_years=2.0),
        card(
            key_skills=[{"name": "Python"}, {"name": "PostgreSQL"}],
            experience={"id": "between1And3", "name": "От 1 до 3 лет"},
            salary={"from": 50000, "to": 100000, "currency": "RUR"},
            area={"name": "Москва"},
        ),
    )
    assert result.score == 70
    assert result.skills == 0.5 and result.title_keywords == 1
    assert result.experience == 1 and result.salary_region == 1
    assert result.matched_skills == ["python"]
    assert result.missing_skills == ["postgresql"]


def test_full_match_is_100():
    result = evaluate(
        profile(
            title="Python Developer",
            skills=["python"],
            experience_years=3.0,
            salary=None,
            region=None,
        ),
        card(key_skills=[{"name": "Python"}], experience={"id": "between3And6", "name": "3–6"}),
    )
    assert result.score == 100


def test_unknown_data_is_zero_when_preferences_exist():
    result = evaluate(profile(), card())
    assert result.score == 0
    assert result.matched_skills == result.missing_skills == []


def test_normalization_aliases_and_duplicates():
    result = evaluate(
        profile(skills=[" PostgreSQL ", "JS", "C#"]),
        card(
            key_skills=[
                {"name": "POSTGRES"},
                {"name": " PostgreSQL "},
                {"name": "JavaScript"},
                {"name": "csharp"},
            ]
        ),
    )
    assert result.skills == 1
    assert result.matched_skills == ["c#", "javascript", "postgresql"]
    assert result.score == 60


def test_extra_candidate_skills_do_not_reduce_score():
    result = evaluate(
        profile(skills=["python", "java", "sql"]), card(key_skills=[{"name": "Python"}])
    )
    assert result.skills == 1 and result.missing_skills == []


def test_no_required_skills_does_not_invent_matches():
    result = evaluate(profile(skills=["python"]), card(description="Python", key_skills=None))
    assert result.skills == 0 and result.matched_skills == []


def test_html_entities_and_keyword_phrases():
    result = evaluate(
        profile(keywords=["machine learning", "research & development"]),
        card(description="<p>Machine <b>learning</b></p><p>Research &amp; development</p>"),
    )
    assert result.title_keywords == 1


@pytest.mark.parametrize(
    ("term", "text", "expected"),
    [
        ("java", "JavaScript developer", 0),
        ("sql", "PostgreSQL", 0),
        ("c++", "C++ developer", 1),
        ("c#", "C# developer", 1),
        (".net", "ASP.NET developer", 0),
        (".net", ".NET developer", 1),
        ("python", "PYTHON-разработчик", 1),
    ],
)
def test_keyword_boundaries(term, text, expected):
    assert evaluate(profile(keywords=[term]), card(name=text)).title_keywords == expected


@pytest.mark.parametrize(
    ("years", "identifier", "expected"),
    [
        (0.0, "noExperience", 1),
        (0.0, "between1And3", 0),
        (1.0, "between1And3", 1),
        (3.0, "between3And6", 1),
        (8.0, "between1And3", 1),
        (6.0, "moreThan6", 0),
        (6.1, "moreThan6", 1),
        (None, "noExperience", 0),
        (3.0, "unknown", 0),
    ],
)
def test_experience_boundaries(years, identifier, expected):
    assert (
        evaluate(
            profile(experience_years=years), card(experience={"id": identifier, "name": "test"})
        ).experience
        == expected
    )


@pytest.mark.parametrize(
    ("salary", "expected"),
    [
        ({"from": 50000, "to": 80000, "currency": "RUR"}, 0.4),
        ({"from": 150000, "currency": "RUR"}, 0.5),
        ({"to": 100000, "currency": "USD"}, 0),
        ({"from": None, "to": None, "currency": "RUR"}, 0),
        (None, 0),
    ],
)
def test_salary_ranges_and_currency(salary, expected):
    assert evaluate(profile(), card(salary=salary)).salary_region == expected


def test_region_normalization_and_no_preferences():
    assert (
        evaluate(profile(region="  МОСКВА  "), card(area={"name": "Москва"})).salary_region == 0.5
    )
    assert evaluate(profile(salary=None, region=None), card()).salary_region == 1


def test_sorting_ties_are_stable_across_input_order():
    items = [card(id="10"), card(id="2"), card(id="1", key_skills=[{"name": "Python"}])]
    candidate = profile(skills=["Python"])
    first = rank_vacancies(candidate, items)
    assert [item.id for item, _ in first] == ["1", "2", "10"]
    assert rank_vacancies(candidate, list(reversed(items))) == first
    assert rank_vacancies(candidate, []) == []


def test_fractional_score_is_rounded():
    result = evaluate(
        profile(skills=["python"]),
        card(
            key_skills=[
                {"name": name} for name in ["python", "java", "sql", "go", "rust", "c", "c++"]
            ]
        ),
    )
    assert result.score == 8.57
