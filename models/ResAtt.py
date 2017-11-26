import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.autograd import *
import misc.utils as utils

from .CaptionModel import CaptionModel
from .AttModel import AttModel


class CBN2D(nn.Module):
    def __init__(self, feat_size, momentum=0.1):
        super(CBN2D, self).__init__()
        self.feat_size = feat_size
        # self.gamma = Parameter(torch.Tensor(self.feat_size))
        # self.beta = Parameter(torch.Tensor(self.feat_size))
        self.var = Parameter(torch.Tensor(self.feat_size))
        self.miu = Parameter(torch.Tensor(self.feat_size))
        self.eps = 1e-9
        self.momentum = momentum
        self.reset_param()

    def forward(self, x, gatta=None):
        assert(x.dim() == 4)
        gamma = None
        beta = None
        if gatta:
            gamma = gatta[:self.feat_size]
            beta = gatta[self.feat_size:]
        x_size = x.size()
        x = x.view(-1, self.feat_size)
        tmp_mean = torch.mean(x, 0)
        tmp_var = torch.var(x, 0)
        self.out = (x - self.miu) / torch.sqrt(self.var +
                                               self.eps) * (1 + self.gamma or Variable(torch.zeros(self.feat_size))) + (self.beta or Variable(torch.zeros(self.feat_size)))

        if self.training:
            self.miu = self.momentum * self.miu + \
                (1 - self.momentum) * tmp_mean

            self.var = self.momentum * self.momentum + \
                (1 - self.momentum) * tmp_var

    def reset_param(self):
        self.miu.data.zero_()
        self.var.data.fill_(1)


class ResBlk(nn.Module):

    def __init__(self, opt):
        super(ResBlk, self).__init__()
        self.conv1 = nn.Sequential(
            nn.Conv2d(opt.rnn_size, opt.rnn_size, 1), nn.ReLU())
        self.conv2 = nn.Conv2(
            nn.Conv2d(opt.rnn_size, opt.rnn_size, 3, padding=1))
        self.bn1 = CBN2D(opt.rnn_size)
        self.conv3 = nn.Conv2(
            nn.Conv2d(opt.rnn_size, opt.rnn_size, 3, padding=1))
        self.bn2 = CBN2D(opt.rnn_size)
        self.alpha_beta1 = nn.Sequential(
            nn.Linear(opt.rnn_size, opt.rnn_size * 2), nn.ReLU())
        self.alpha_beta2 = nn.Sequential(
            nn.Linear(opt.rnn_size, opt.rnn_size * 2), nn.ReLU())

    def forward(self, att_feat, embed_xt=None):
        gatta1 = None
        gatta2 = None
        if embed_xt:
            gatta1 = self.alpha_beta1(embed_xt)
            gatta2 = self.alpha_beta2(embed_xt)
        res = self.conv1(att_feat)
        x = self.bn1(res, gatta1)
        F.relu_(x)
        x = self.conv3(x)
        x = self.bn2(x)
        x = F.relu(x + res)
        return x


class ResSeq(nn.Module):
    def __init__(self, opt):
        self.reslist = nn.ModuleList(ResBlk(opt)
                                     for i in range(opt.resblock_num))
        self.resblock_num = opt.resblock_num
        self.pool = nn.MaxPool2d(14)

    def forward(self, att_feat, embed_xt):
        for i in range(self.resblock_num):
            x = self.reslist[i](att_feat, embed_xt)
        x = self.pool(x)
        return torch.squeeze(x)


class ResCore(nn.Module):
    def __init__(self, opt):
        super(ResCore, self).__init__()
        self.lstm = nn.LSTMCore(
            opt.input_encoding_size + opt.rnn_size * 2, opt.rnn_size)
        self.resblocks = ResSeq(opt)
        self.prev_out = None

    def forward(self, xt, fc_feats, att_feats, p_att_feats, state):
        # xt: batch * 512
        # fc_feats batch*512
        # att_feats batch*512
        # p_att_feats batch*512

        conv_x = att_feats.permmute(0, 2, 3, 1)
        lstm_input = self.resblocks(conv_x, self.prev_out)
        out, state = self.lstm(lstm_input, state)
        self.prev_out = out
        return out, state


class ResModel(AttModel):
    def __init__(self, opt):
        super(ResModel, self).__init__(opt)
        self.core = ResCore(opt)