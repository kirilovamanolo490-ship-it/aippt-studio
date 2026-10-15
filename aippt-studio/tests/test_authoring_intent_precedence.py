import pytest
from aippt_studio.models import JobBrief, PresentationForm
from aippt_studio.presentation_intent import select_presentation_intent


@pytest.mark.parametrize('text,form', [
    ('这是品牌部年度工作汇报，画面要高端华丽。', PresentationForm.BUSINESS_REPORT),
    ('做课题进展汇报，向管理层说明研究工作进度和下一步。', PresentationForm.BUSINESS_REPORT),
    ('这是技术架构说明，风格可以有电影感。', PresentationForm.TECHNICAL_EXPLANATION),
    ('给政府工作人员做技术培训课程，解释系统操作。', PresentationForm.TEACHING),
    ('给学生上品牌设计教学课程，研究案例只是教学材料。', PresentationForm.TEACHING),
    ('为政府领导进行正式防汛工作汇报。', PresentationForm.GOVERNMENT_BRIEFING),
    ('制作毕业论文答辩，说明研究对企业决策的意义。', PresentationForm.ACADEMIC_DEFENSE),
    ('职业规划大赛，说明学生的成长经历和行动计划。', PresentationForm.COMPETITION_DEFENSE),
    ('将论文答辩改为工作汇报，面向部门负责人。', PresentationForm.BUSINESS_REPORT),
    ('工作汇报改成培训课程，先解释基础概念。', PresentationForm.TEACHING),
    ('论文答辩不要改为工作汇报，保留学术结构。', PresentationForm.ACADEMIC_DEFENSE),
    ('论文答辩中，把标题改为工作汇报，其他结构不变。', PresentationForm.ACADEMIC_DEFENSE),
])
def test_specific_occasion_precedes_style_or_subject(text, form):
    result = select_presentation_intent(JobBrief(goal='当前任务'), {}, request_text=text)
    assert result.presentation_form == form
