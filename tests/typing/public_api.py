"""Static consumer contract; checked with mypy, never executed against IS MU."""

from typing import assert_type

from ismu import Answer, Client, Question, RopotSession, models


def use_client(client: Client, code: str, attempt: RopotSession) -> None:
    assert_type(client.login("example", "synthetic"), models.LoginResult)
    assert_type(client.status(), models.Status)
    assert_type(client.courses(), list[models.Course])
    assert_type(client.course(code), models.Course)
    assert_type(client.files(code), models.Files)
    assert_type(client.read_file(code, "um/example.txt"), models.FileRead)
    assert_type(client.syllabus(code), models.Syllabus)
    assert_type(client.ropots(code), models.Ropots)
    assert_type(client.forums(code), models.Forums)
    assert_type(client.notes(), models.Notes)
    assert_type(client.reservations(code), models.Reservations)
    assert_type(client.open_submissions(), models.OpenSubmissions)
    assert_type(client.submissions(code), models.Submissions)
    assert_type(client.submission_box(code, "ode/example/"), models.SubmissionBox)
    assert_type(client.calendar(), models.Calendar)
    assert_type(client.timetable(), models.Timetable)
    assert_type(client.exams(), models.Exams)
    assert_type(client.mail_folders(), models.MailFolders)
    assert_type(client.mail_messages(), models.MailListing)
    assert_type(client.mail_message("1", folder="2", allow_mark_read=True), models.MailMessage)
    assert_type(
        client.download_mail("1", folder="2", destination="example.eml", allow_mark_read=True),
        models.MailDownload,
    )
    assert_type(client.notices(), models.Notices)
    assert_type(client.notice("1", allow_mark_read=True), models.NoticeDetail)
    assert_type(client.snapshot(), models.Snapshot)
    assert_type(client.courses()[0]["code"], str)
    assert_type(client.ropots(code)["items"][0]["availability"][0]["closes"], str | None)
    assert_type(client.forums(code)["forums"][0]["posts"][0]["text"], str)
    assert_type(client.ropot(client.ropots(code)["items"][0]), RopotSession)
    assert_type(attempt.inspect(), models.AttemptView)
    assert_type(attempt.start(), models.AttemptView)
    assert_type(attempt.questions(), models.AttemptView)
    assert_type(attempt.question_list(), list[Question])
    question = attempt.question(1)
    assert_type(question, Question)
    answer = question.answer("5*x^4")
    assert_type(answer, Answer)
    assert_type(attempt.save(answer), models.AttemptView)
    assert_type(attempt.submit([answer], confirm=True), models.AttemptView)
    assert_type(attempt.save({"tst_example": "value"}), models.AttemptView)
    # Expected failures prove the public signatures are not silently Any.
    question.answer(123)  # type: ignore[arg-type]
    client.courses()[0]["typo"]  # type: ignore[typeddict-item]
    client.calendar(start=123)  # type: ignore[arg-type]
    attempt.save({"tst_example": 123})  # type: ignore[dict-item]
