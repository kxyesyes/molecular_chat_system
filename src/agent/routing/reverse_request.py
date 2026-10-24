"""Fresh reverse-target routing views; never grant scientific execution."""
import re

from src.agent.utils.validators import InputValidator


def _fresh_molecule_reverse_request(message):
    # Bind an affirmative reverse action to its molecule, not an arbitrary
    # ligand mentioned in a reference to an earlier ranking. Dots belong to
    # disconnected SMILES, not clauses.
    clauses = re.split(r'[，,;；。!?！？\n]|\b(?:and|but|then)\b|并且|然后', message, flags=re.I)
    chinese_prefix = r'\s*(?:请\s*)?(?:重新\s*)?'
    chinese_action = r'(?:反向寻靶|反向靶点预测|反向预测靶点)'
    chinese_display = (r'(?:\s*并\s*(?:列出|展示|显示)\s*'
                       r'(?:排名第[一1]|第[一1]名)的(?:候选)?靶点)?\s*')
    for clause in clauses:
        for molecule in InputValidator().extract_potential_smiles_candidates(clause):
            token = re.escape(molecule)
            label = r'(?:SMILES\s*[:：]\s*)?'
            if (re.fullmatch(chinese_prefix + r'对\s*' + label + token
                             + r'\s*(?:做|进行|运行)\s*' + chinese_action + chinese_display, clause, re.I)
                    or re.fullmatch(chinese_prefix + r'(?:(?:做|进行|运行)\s*)?'
                                    + chinese_action + r'\s*(?:对\s*)?' + label + token + chinese_display, clause, re.I)
                    or re.fullmatch(r'\s*(?:please\s+)?(?:(?:run|perform|rerun|predict)\s+)?'
                                    r'reverse[ -]target(?:s|ing)?\s+(?:prediction\s+)?(?:for\s+)?'
                                    + label + token + r'\s*', clause, re.I)):
                return True
    return False


def reverse_rank_routing_query(message):
    """Mask only fresh display qualifiers; execution/history keep the original."""
    if not _fresh_molecule_reverse_request(message):
        return message
    return re.sub(r'((?:列出|展示|显示)\s*)(?:排名第[一1]|第[一1]名)的((?:候选)?靶点)',
                  r'\1\2', message)


def reverse_molecular_input_query(message):
    """Recognize a Chinese display-clause boundary, not a molecular suffix.

    The complete structure before this boundary still passes the strict
    parser. No text is discarded and no arbitrary comma is a delimiter.
    """
    return re.sub(r'，(?=(?:列出|展示|显示)\s*(?:排名第[一1]|第[一1]名)的'
                  r'(?:候选)?靶点(?:[，,;；。]|$))', ';', message)
