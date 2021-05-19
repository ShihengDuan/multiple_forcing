
from torch import nn
import torch
from torch.nn.utils import weight_norm
from typing import Dict
from codebase.modelzoo.head import get_head
from codebase.modelzoo.basemodel import BaseModel

class Chomp1d(nn.Module): # causal padding
    def __init__(self, chomp_size):
        super(Chomp1d, self).__init__()
        self.chomp_size = chomp_size

    def forward(self, x):
        return x[:, :, :-self.chomp_size].contiguous()

class TemporalBlock(nn.Module):
    def __init__(self, n_inputs, n_outputs, kernal_size, stride, dilation, padding, dropout=0.4):
        super(TemporalBlock, self).__init__()
        self.conv1 = weight_norm(nn.Conv1d(n_inputs, n_outputs, kernal_size, stride=stride,
                                          padding=padding, dilation=dilation))
        self.chomp1 = Chomp1d(padding)
        self.relu1 = nn.ReLU()
        self.dropout1 = nn.Dropout(dropout)

        self.conv2 = weight_norm(nn.Conv1d(n_outputs, n_outputs, kernal_size,
                                           stride=stride, padding=padding, dilation=dilation))
        self.chomp2 = Chomp1d(padding)
        self.relu2 = nn.ReLU()
        self.dropout2 = nn.Dropout(dropout)

        self.net = nn.Sequential(self.conv1, self.chomp1, self.relu1, self.dropout1,
                                 self.conv2, self.chomp2, self.relu2, self.dropout2)
        self.downsample = nn.Conv1d(n_inputs, n_outputs, 1) if n_inputs != n_outputs else None # res connection
        self.relu = nn.ReLU()
        # self.init_weights()
    def forward(self, x):
        out = self.net(x)
        res = x if self.downsample is None else self.downsample(x)
        return self.relu(out + res) # res connection


    def init_weights(self):
        self.conv1.weight.data.uniform_(-0.1, 0.1)
        self.conv2.weight.data.uniform_(-0.1, 0.1)
        if self.downsample is not None:
            self.downsample.weight.data.normal_(0, 0.01)




class TCNN(BaseModel):
    def __init__(self, cfg: Dict):
        super(TCNN, self).__init__(cfg=cfg)

        self.kernal_size = cfg["kernal_size"]
        self.num_levels = cfg["num_levels"]
        self.num_channels = cfg["num_channels"]
        self.dr_rate = 0.4

        n_attributes = 0
        if ("camels_attributes" in cfg.keys()) and cfg["camels_attributes"]:
            print('input attributes')
            n_attributes += len(cfg["camels_attributes"])

        self.input_size = len(cfg["dynamic_inputs"] + cfg.get("static_inputs", [])) + n_attributes
        if cfg["use_basin_id_encoding"]:
            self.input_size += cfg["number_of_basins"]

        layers = []
        # num_levels = len(num_channels) # number of blocks. Should be 2-3. maybe more?
        
        for i in range(self.num_levels):
            # dilation_size = 2 ** i # dilation rate with layer number
            dilation_size = 6*(i+1)
            in_channels = self.input_size if i == 0 else self.num_channels
            out_channels = self.num_channels
            layers += [TemporalBlock(in_channels, out_channels, padding=(self.kernal_size-1) * dilation_size, stride=1, dilation=dilation_size,
                                      dropout=self.dr_rate, kernal_size=self.kernal_size)]

        self.tcnn = nn.Sequential(*layers)

        self.dropout = nn.Dropout(p=cfg["output_dropout"])

        # self.head = get_head(cfg=cfg, n_in=20*20, n_out=self.output_size) ## Maybe final layer

        # self.reset_parameters()
        self.dense1 = nn.Linear(self.num_channels*20, 100)
        self.act = nn.ReLU()
        self.dense2 = nn.Linear(100,1)
        self.flat = nn.Flatten()

    def forward(self, x_d: torch.Tensor, x_s: torch.Tensor, x_one_hot: torch.Tensor):
        # transpose to [seq_length, batch_size, n_features]
        x_d = x_d.transpose(0, 1)
        # original: batch_size, length, features
        # to [batch_size, n_features, seq_length]

        # concat all inputs
        if (x_s.nelement() > 0) and (x_one_hot.nelement() > 0):
            x_s = x_s.unsqueeze(0).repeat(x_d.shape[0], 1, 1)
            x_one_hot = x_one_hot.unsqueeze(0).repeat(x_d.shape[0], 1, 1)
            x_d = torch.cat([x_d, x_s, x_one_hot], dim=-1)
        elif x_s.nelement() > 0:
            x_s = x_s.unsqueeze(0).repeat(x_d.shape[0], 1, 1)
            x_d = torch.cat([x_d, x_s], dim=-1)
        elif x_one_hot.nelement() > 0:
            x_one_hot = x_one_hot.unsqueeze(0).repeat(x_d.shape[0], 1, 1)
            x_d = torch.cat([x_d, x_one_hot], dim=-1)
        else:
            pass
        ## convert to CNN inputs:
        x_d = x_d.transpose(0,1)
        x_d = x_d.transpose(1,2)
        tcnn_out = self.tcnn(input=x_d)
        ## slice:
        tcnn_out = tcnn_out[:,:,-20:]
        # import pdb
        # pdb.set_trace()
        y_hat = self.dense2(self.dropout(self.act(self.dense1(self.flat(tcnn_out)))))
        # y_hat = self.head(self.flat(tcnn_out))
        y_hat = y_hat.unsqueeze(1)

        return y_hat, tcnn_out, x_d # keep the same form with LSTM's other two outputs


