import torch.nn as nn
import torch.nn.functional as F
import numpy as np
from  torch.utils.data import Dataset,DataLoader
from Config import *
from torch.utils import data
from transformers import BertTokenizer
from transformers import BertModel
from sklearn.metrics import classification_report
import torch
import matplotlib.pyplot as plt

def read_data(filename, num=None):
    with open(filename, encoding="utf-8") as f:
        all_data = f.read().split("\n")

    texts = []
    labels = []
    for data in all_data:
        if data:
            t,l = data.split("\t")
            texts.append(t)
            labels.append(l)
    if num == None:
        return texts,labels
    else:
        return texts[:num],labels[:num]


class Dataset(data.Dataset):
    def __init__(self, type='train'):
        super().__init__()
        if type == 'train':
            sample_path = TRAIN_PATH
        elif type == 'test':
            sample_path = TEST_PATH

        self.lines = open(sample_path, encoding='utf-8').readlines()
        self.tokenizer = BertTokenizer.from_pretrained(BERT_MODEL)

    def __len__(self):
        return len(self.lines)

    def __getitem__(self, index):
        text, label = self.lines[index].split('\t')
        tokened = self.tokenizer(text)
        input_ids = tokened['input_ids']
        mask = tokened['attention_mask']

        if len(input_ids) < MAX_LEN:
            pad_len = (MAX_LEN - len(input_ids))
            input_ids += [BERT_PAD_ID] * pad_len
            mask += [0] * pad_len

        return torch.tensor(input_ids[:MAX_LEN]), torch.tensor(mask[:MAX_LEN]), torch.tensor(int(label))


class BERT_TextCNN_BiLSTM(nn.Module):
    def __init__(self):
        super(BERT_TextCNN_BiLSTM, self).__init__()
        self.bert = BertModel.from_pretrained(BERT_MODEL)
        for name, param in self.bert.named_parameters():
            param.requires_grad = False

        self.conv1 = nn.Conv2d(in_channels=1, out_channels=HIDDEN_DIM, kernel_size=(3, EMBEDDING))
        self.conv2 = nn.Conv2d(in_channels=1, out_channels=HIDDEN_DIM, kernel_size=(5, EMBEDDING))
        self.conv3 = nn.Conv2d(in_channels=1, out_channels=HIDDEN_DIM, kernel_size=(7, EMBEDDING))

        self.lstm = nn.LSTM(input_size=EMBEDDING, hidden_size=HIDDEN_DIM, num_layers=N_LAYERS, batch_first=True, bidirectional=True)

        self.dropout = nn.Dropout(DROP_PROB)
        self.linear = nn.Linear(HIDDEN_DIM*2+HIDDEN_DIM*3, CLASS_NUM)
        # self.linear_textcnn = nn.Linear(HIDDEN_NUM*3, CLASS_NUM)
        # self.linear_bilstm = nn.Linear(HIDDEN_DIM*2, OUTPUT_SIZE)


    def conv_and_pool(self, conv, input):
        out = conv(input)
        out = F.relu(out)
        # out.shape[2]: MAX_LEN - kernel_num + 1
        # out.shape[3]: 1
        # 池化不会改变形状，最后两维是1，所以降维
        return F.max_pool2d(out, (out.shape[2], out.shape[3])).squeeze()


    def init_hidden(self, batch_size):
        weight = next(self.parameters()).data
        number = 2
        hidden = (weight.new(N_LAYERS * number, BATH_SIZE, HIDDEN_DIM).zero_().float(),
                  weight.new(N_LAYERS * number, BATH_SIZE, HIDDEN_DIM).zero_().float()
                  )

        return hidden


    def forward(self, input, mask, hidden):
        # 先输出 (batch, max_len, embedding) = (10, 20, 768), 进行升维，后面做二维卷积
        bert_cnn_out = self.bert(input, mask)[0].unsqueeze(1)   # 得到词向量
        bert_bilstm_out = self.bert(input, mask)[0]
        cnn_out1 = self.conv_and_pool(self.conv1, bert_cnn_out)
        cnn_out2 = self.conv_and_pool(self.conv2, bert_cnn_out)
        cnn_out3 = self.conv_and_pool(self.conv3, bert_cnn_out)
        cnn_out = torch.cat([cnn_out1, cnn_out2, cnn_out3], dim=1)

        lstm_out, (hidden_last, cn_last) = self.lstm(bert_bilstm_out, hidden)

        # 正向最后一层，最后一个时刻
        hidden_last_L = hidden_last[-2]  # [batch_size, hidden_num]
        # 反向最后一层，最后一个时刻
        hidden_last_R = hidden_last[-1]  # []
        # 进行拼接
        hidden_last_out = torch.cat([hidden_last_L, hidden_last_R], dim=-1)  # [batch_size, hidden_num*2]

        all_out = torch.cat([cnn_out, hidden_last_out], dim=-1)

        all_out = self.dropout(all_out)

        return self.linear(all_out)



if __name__ == "__main__":

    DEVICE = 'cuda' if torch.cuda.is_available() else 'cpu'

    train_text, train_label = read_data(TRAIN_PATH)
    test_text, test_label = read_data(TEST_PATH)

    train_dataset = Dataset('train')
    train_loader = data.DataLoader(train_dataset, batch_size=BATH_SIZE, shuffle=True, drop_last=True)

    test_dataset = Dataset('test')
    test_loader = data.DataLoader(test_dataset, batch_size=BATH_SIZE, shuffle=True, drop_last=True)

    model = BERT_TextCNN_BiLSTM().to(DEVICE)
    optimizer = torch.optim.Adam(model.parameters(), lr=LR)
    loss_fn = nn.CrossEntropyLoss()

    accuracy_list = []
    precision_list = []
    recall_list = []
    f1_score_list = []
    for e in range(1):
        times = 0
        h = model.init_hidden(BATH_SIZE)
        for b, (input, mask, target) in enumerate(train_loader):
            input = input.to(DEVICE)
            mask = mask.to(DEVICE)
            target = target.to(DEVICE)

            h = tuple([each.data for each in h])

            pred = model(input, mask, h)
            loss = loss_fn(pred, target)
            times += 1
            print(f"loss:{loss:.3f}")

            optimizer.zero_grad()  # 梯度初始化为 0
            loss.backward()   # 反向传播求梯度
            optimizer.step()  # 更新所有参数

        # ------------------  Test  ------------------------

        true_lable_list = []
        pred_lable_list = []
        TP = 0
        TN = 0
        FP = 0
        FN = 0
        h = model.init_hidden(BATH_SIZE)
        for b, (test_input, test_mask, test_target) in enumerate(test_loader):
            test_input = test_input.to(DEVICE)
            test_mask = test_mask.to(DEVICE)
            test_target = test_target.to(DEVICE)

            h = tuple([each.data for each in h])

            test_pred = model(test_input, test_mask, h)
            test_pred_ = torch.argmax(test_pred, dim=1)
            true_lable_list = test_target.cpu().numpy().tolist()
            pred_lable_list = test_pred_.cpu().numpy().tolist()
            for i in range(len(true_lable_list)):
                if pred_lable_list[i] == 0 and true_lable_list[i] == 0:
                    TP += 1
                if pred_lable_list[i] == 1 and true_lable_list[i] == 1:
                    TN += 1
                if pred_lable_list[i] == 0 and true_lable_list[i] == 1:
                    FP += 1
                if pred_lable_list[i] == 1 and true_lable_list[i] == 0:
                    FN += 1
        accuracy = (TP + TN) * 1.0 / (TP + TN + FP + FN)
        precision = TP * 1.0 / (TP + FP) * 1.0
        recall = TP * 1.0 / (TP + FN)
        f1_score = 2.0 * precision * recall / (precision + recall)
        accuracy_list.append(format(accuracy * 100, '.2f'))
        precision_list.append(format(precision * 100, '.2f'))
        recall_list.append(format(recall * 100, '.2f'))
        f1_score_list.append(format(f1_score * 100, '.2f'))
        print(accuracy)
        print('---------------------')

    print(accuracy_list)
    print(precision_list)
    print(recall_list)
    print(f1_score_list)




