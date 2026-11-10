'''
AttentiveFP shorted as AFP
'''

import torch
import torch.nn.functional as F
from torch.nn import GRUCell, Linear
from torch_geometric.nn import GATConv, global_add_pool
from torch_scatter import scatter

from .ReduceGNN import GATEConv, glorot, zeros


class AFP(torch.nn.Module):

    def __init__(self, in_channels: int, hidden_channels: int,
                 out_channels: int, edge_dim: int, num_layers: int,
                 num_timesteps: int, dropout: float = 0.0):
        super(AFP, self).__init__()

        self.num_layers = num_layers
        self.num_timesteps = num_timesteps
        self.dropout = dropout

        self.lin1 = Linear(in_channels, hidden_channels)

        conv = GATEConv(hidden_channels, hidden_channels, edge_dim, dropout)
        gru = GRUCell(hidden_channels, hidden_channels)
        self.atom_convs = torch.nn.ModuleList([conv])
        self.atom_grus = torch.nn.ModuleList([gru])
        for _ in range(num_layers - 1):
            conv = GATConv(hidden_channels, hidden_channels, dropout=dropout,
                           add_self_loops=False, negative_slope=0.01)
            self.atom_convs.append(conv)
            self.atom_grus.append(GRUCell(hidden_channels, hidden_channels))

        self.mol_conv = GATConv(hidden_channels, hidden_channels,
                                dropout=dropout, add_self_loops=False,
                                negative_slope=0.01)
        self.mol_gru = GRUCell(hidden_channels, hidden_channels)

        self.lin2 = Linear(hidden_channels, out_channels)

        self.reset_parameters()

    def reset_parameters(self):
        self.lin1.reset_parameters()
        for conv, gru in zip(self.atom_convs, self.atom_grus):
            conv.reset_parameters()
            gru.reset_parameters()
        self.mol_conv.reset_parameters()
        self.mol_gru.reset_parameters()
        self.lin2.reset_parameters()

    def forward(self, data, rg_data):
        x, edge_index, edge_attr, batch = data.x, data.edge_index, data.edge_attr, data.batch
        
        # Atom Embedding:
        x = F.leaky_relu_(self.lin1(x))

        h = F.elu_(self.atom_convs[0](x, edge_index, edge_attr))
        h = F.dropout(h, p=self.dropout, training=self.training)
        x = self.atom_grus[0](h, x).relu_()

        for conv, gru in zip(self.atom_convs[1:], self.atom_grus[1:]):
            h = F.elu_(conv(x, edge_index))
            h = F.dropout(h, p=self.dropout, training=self.training)
            x = gru(h, x).relu_()

        # Molecule Embedding:
        row = torch.arange(batch.size(0), device=batch.device)
        edge_index = torch.stack([row, batch], dim=0)

        out = global_add_pool(x, batch).relu_()
        for t in range(self.num_timesteps):
            h = F.elu_(self.mol_conv((x, out), edge_index))
            h = F.dropout(h, p=self.dropout, training=self.training)
            out = self.mol_gru(h, out).relu_()

        # Predictor:
        fp = F.dropout(out, p=self.dropout, training=self.training)
        out = (self.lin2(fp)).view(-1)
        return out, fp

class ReduceAFP(torch.nn.Module):

    def __init__(self, in_channels, channels,
                 out_channels, edge_dim, 
                 num_passing_atom=2, 
                 num_passing_pool=2,
                 num_passing_rg=2,
                 num_passing_mol=2,
                 dropout=0.0):
        super(ReduceAFP, self).__init__()

        self.num_passing_atom = num_passing_atom
        self.num_passing_pool = num_passing_pool
        self.num_passing_rg = num_passing_rg
        self.num_passing_mol = num_passing_mol

        self.dropout = dropout
        self.lin1 = Linear(in_channels, channels)
        # atom-level
        conv = GATEConv(channels, channels, edge_dim, dropout)
        gru = GRUCell(channels, channels)
        self.atom_convs = torch.nn.ModuleList([conv])
        self.atom_grus = torch.nn.ModuleList([gru])
        for _ in range(num_passing_atom - 1):
            conv = GATConv(channels, channels, dropout=dropout,
                           add_self_loops=False, negative_slope=0.01)
            self.atom_convs.append(conv)
            self.atom_grus.append(GRUCell(channels, channels))

        # atom-RG
        self.pconv = GATConv(channels, channels, 
                                add_self_loops=False,
                                negative_slope=0.01)
        self.pgru = GRUCell(channels, channels)

        # RG-level
        self.lin_rg = torch.nn.Linear(channels+18, channels) 
        rg_econv = GATEConv(channels, channels, 1, dropout)
        rg_gru = GRUCell(channels, channels)
        self.rg_convs = torch.nn.ModuleList([rg_econv])
        self.rg_grus = torch.nn.ModuleList([rg_gru])
        for _ in range(num_passing_rg - 1):
            rg_conv = GATConv(channels, channels, dropout=dropout,
                           add_self_loops=False, negative_slope=0.01)
            self.rg_convs.append(rg_conv)
            self.rg_grus.append(GRUCell(channels, channels))


        # RG-molecule
        self.mol_conv = GATConv(channels, channels,
                                dropout=dropout, add_self_loops=False,
                                negative_slope=0.01)
        self.mol_gru = GRUCell(channels, channels)

        self.lin2 = Linear(channels, out_channels)

        self.reset_parameters()

    def reset_parameters(self):
        self.lin1.reset_parameters()
        for conv, gru in zip(self.atom_convs, self.atom_grus):
            conv.reset_parameters()
            gru.reset_parameters()
        self.pconv.reset_parameters()
        self.pgru.reset_parameters()
        self.lin_rg.reset_parameters()
        for rg_conv, rg_gru in zip(self.rg_convs, self.rg_grus):
            rg_conv.reset_parameters()
            rg_gru.reset_parameters()
        self.mol_conv.reset_parameters()
        self.mol_gru.reset_parameters()
        self.lin2.reset_parameters()

    def forward(self, data, rg_data):
        x, edge_index, edge_attr, batch = data.x, data.edge_index, data.edge_attr, data.batch
        rg_batch,rg_edge_index,rg_edge_attr = rg_data.batch, rg_data.edge_index, rg_data.edge_attr
        
        # mpnn of atom-level
        x = F.leaky_relu_(self.lin1(x))

        h = F.elu_(self.atom_convs[0](x, edge_index, edge_attr))
        h = F.dropout(h, p=self.dropout, training=self.training)
        x = self.atom_grus[0](h, x).relu_()

        for conv, gru in zip(self.atom_convs[1:], self.atom_grus[1:]):
            h = F.elu_(conv(x, edge_index))
            h = F.dropout(h, p=self.dropout, training=self.training)
            out = gru(h, x).relu_()
        
        # atom-RG
        pool_index = data.pool_index.long()
        tid,sid = pool_index 
        mask = torch.unique(tid) # [R]
        pool_index = torch.stack([sid, tid],dim=0) 

        rg_out_ = scatter(out[sid], tid, dim=0, reduce='add') 
        rg_out = rg_out_[mask]

        for i in range(self.num_passing_pool):       
            h = F.elu_(self.pconv((out,rg_out_),pool_index)) # [R_,C]
            h = h[mask] # [R,C]
            rg_out = self.pgru(h, rg_out).relu_() # [R,C]
         
        # mpnn of rg-level
        rg_out = torch.cat([rg_out,rg_data.x],dim=1) 
        rg_out = self.lin_rg(rg_out)

        h = F.elu_(self.rg_convs[0](rg_out, rg_edge_index, rg_edge_attr))
        h = F.dropout(h, p=self.dropout, training=self.training)
        rg_out = self.rg_grus[0](h, rg_out).relu_() 

        for rg_conv, rg_gru in zip(self.rg_convs[1:], self.rg_grus[1:]):  
            h = F.elu_(rg_conv(rg_out, rg_edge_index))
            h = F.dropout(h, p=self.dropout, training=self.training)
            rg_out = rg_gru(h, rg_out).relu_()


        # readout at molecule level
        row = torch.arange(rg_batch.size(0), device=batch.device)
        edge_index = torch.stack([row, rg_batch], dim=0)

        out = global_add_pool(rg_out, rg_batch).relu_()
        for t in range(self.num_passing_mol):
            h = F.elu_(self.mol_conv((rg_out, out), edge_index))
            h = F.dropout(h, p=self.dropout, training=self.training)
            out = self.mol_gru(h, out).relu_()

        # Predictor:
        fp = F.dropout(out, p=self.dropout, training=self.training)
        out = self.lin2(fp).view(-1)
        return out, fp
