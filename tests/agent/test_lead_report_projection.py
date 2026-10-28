from src.web.scientific_report import _report_execution


def test_report_projection_keeps_lead_optimization_workflow_plan():
    execution = {
        "trace_id": "trace",
        "metadata": {
            "task_requirements": {
                "profile": "medchat-web-v1",
                "workflow_name": "hit_to_lead_optimization",
                "metadata": {},
            }
        },
    }

    projected = _report_execution(execution)

    assert projected["workflow_plan"]["workflow_name"] == "hit_to_lead_optimization"


def test_report_projection_keeps_molecular_design_workflow_plan():
    execution = {
        "trace_id": "trace",
        "metadata": {
            "task_requirements": {
                "profile": "medchat-web-v1",
                "workflow_name": "molecular_design",
                "metadata": {},
            }
        },
    }

    projected = _report_execution(execution)

    assert projected["workflow_plan"]["workflow_name"] == "molecular_design"
