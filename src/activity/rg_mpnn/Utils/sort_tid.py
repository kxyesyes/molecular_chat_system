import torch

def sort_tid(tid):
    '''
    把不连续的tid转成连续的需序号
    '''
    _, inverse_indices = torch.unique(tid, sorted=True, return_inverse=True)
    return inverse_indices
