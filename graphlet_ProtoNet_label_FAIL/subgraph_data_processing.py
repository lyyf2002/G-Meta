import os
import torch
from torch.utils.data import Dataset
import numpy as np
import collections
import csv
import random
import pickle
from torch.utils.data import DataLoader
import dgl

class Subgraphs(Dataset):
    """
    put nodes files as :
    root :
        |- subgraphs/*.nx includes all subgraphs for nodes
        |- train.csv
        |- test.csv
        |- val.csv
    NOTICE: meta-learning is different from general supervised learning, especially the concept of batch and set.
    batch: contains several sets
    sets: conains n_way * k_shot for meta-train set, n_way * n_query for meta-test set.
    """

    def __init__(self, root, mode, subgraph_list, subgraph2label, subgraph2center_node, n_way, k_shot, k_query, batchsz):
        """

        :param root: root path of mini-subgraphnet
        :param mode: train, val or test
        :param batchsz: batch size of sets, not batch of subgraphs
        :param n_way:
        :param k_shot:
        :param k_query: num of qeruy subgraphs per class
        """

        self.batchsz = batchsz  # batch of set, not batch of subgraphs
        self.n_way = n_way  # n-way
        self.k_shot = k_shot  # k-shot
        self.k_query = k_query  # for evaluation
        self.setsz = self.n_way * self.k_shot  # num of samples per set
        self.querysz = self.n_way * self.k_query  # number of samples per set for evaluation
        print('shuffle DB :%s, b:%d, %d-way, %d-shot, %d-query' % (
        mode, batchsz, n_way, k_shot, k_query))

        # load subgraph list 

        self.subgraph2label = subgraph2label
        self.subgraph_list = subgraph_list
        self.subgraph2center_node = subgraph2center_node

        csvdata = self.loadCSV(os.path.join(root, mode + '.csv'))  # csv path
        self.data = []

        for i, (k, v) in enumerate(csvdata.items()):
            self.data.append(v)  # [[subgraph1, subgraph2, ...], [subgraph111, ...]]
            #self.subgraph2label[k] = i + self.startidx  # {"subgraph_name[:9]":label}
        self.cls_num = len(self.data)

        self.create_batch(self.batchsz)

    def loadCSV(self, csvf):
        """
        return a dict saving the information of csv
        :param splitFile: csv file name
        :return: {label:[file1, file2 ...]}
        """
        dictLabels = {}
        with open(csvf) as csvfile:
            csvreader = csv.reader(csvfile, delimiter=',')
            next(csvreader, None)  # skip (filename, label)
            for i, row in enumerate(csvreader):
                filename = row[1]
                label = row[2]
                # append filename to current label
                if label in dictLabels.keys():
                    dictLabels[label].append(filename)
                else:
                    dictLabels[label] = [filename]
        return dictLabels

    def create_batch(self, batchsz):
        """
        create batch for meta-learning.
        episode here means batch, and it means how many sets we want to retain.
        :return:
        """
        self.support_x_batch = []  # support set batch
        self.query_x_batch = []  # query set batch
        self.x_batch = []

        for b in range(batchsz):  # for each batch
            # 1.select n_way classes randomly
            #print(self.cls_num)
            #print(self.n_way)
            selected_cls = np.random.choice(self.cls_num, self.n_way, False)  # no duplicate
            np.random.shuffle(selected_cls)
            support_x = []
            query_x = []
            for cls in selected_cls:
                
                # 2. select k_shot + k_query for each class
                selected_subgraphs_idx = np.random.choice(len(self.data[cls]), self.k_shot + self.k_query, False)

                np.random.shuffle(selected_subgraphs_idx)
                indexDtrain = np.array(selected_subgraphs_idx[:self.k_shot])  # idx for Dtrain
                indexDtest = np.array(selected_subgraphs_idx[self.k_shot:])  # idx for Dtest
                support_x.append(
                    np.array(self.data[cls])[indexDtrain].tolist())  # get all subgraphs filename for current Dtrain
                query_x.append(np.array(self.data[cls])[indexDtest].tolist())
                #x.append(np.array(self.data[cls])[np.array(selected_subgraphs_idx)].tolist())
            # shuffle the correponding relation between support set and query set
            random.shuffle(support_x)
            random.shuffle(query_x)
            # support_x: [setsz (k_shot+k_query * n_way)] numbers of subgraphs   
            self.support_x_batch.append(support_x)  # append set to current sets
            self.query_x_batch.append(query_x)  # append sets to current sets
            self.x_batch.append(support_x + query_x)


    def __getitem__(self, index):
        """
        get one task. support_x_batch[index], query_x_batch[index]

        """
        #print(self.support_x_batch[index])

        support_x = [self.subgraph_list[item]  # obtain a list of DGL subgraphs
                             for sublist in self.support_x_batch[index] for item in sublist]
        support_y = np.array([self.subgraph2label[item]  
                              for sublist in self.support_x_batch[index] for item in sublist]).astype(np.int32)
        support_center = np.array([self.subgraph2center_node[item] 
                             for sublist in self.support_x_batch[index] for item in sublist]).astype(np.int32)

        query_x = [self.subgraph_list[item]  # obtain a list of DGL subgraphs
                           for sublist in self.query_x_batch[index] for item in sublist]
        query_y = np.array([self.subgraph2label[item]
                            for sublist in self.query_x_batch[index] for item in sublist]).astype(np.int32)
        query_center = np.array([self.subgraph2center_node[item]
                            for sublist in self.query_x_batch[index] for item in sublist]).astype(np.int32)

        total_center = np.array(list(support_center) + list(query_center))

        # print('global:', support_y, query_y)
        # support_y: [setsz]
        # query_y: [querysz]
        # unique: [n-way], sorted
        unique = np.unique(support_y)
        random.shuffle(unique)
        # relative means the label ranges from 0 to n-way
        support_y_relative = np.zeros(self.setsz)
        query_y_relative = np.zeros(self.querysz)
        for idx, l in enumerate(unique):
            support_y_relative[support_y == l] = idx
            query_y_relative[query_y == l] = idx

        total_y_relative = np.array(list(support_y_relative) + list(query_y_relative))


        # print('relative:', support_y_relative, query_y_relative)
        '''
        code for flatten images:
        for i, path in enumerate(flatten_support_x):
            support_x[i] = self.transform(path)

        for i, path in enumerate(flatten_query_x):
            query_x[i] = self.transform(path)
        # print(support_set_y)
        # return support_x, torch.LongTensor(support_y), query_x, torch.LongTensor(query_y)
        '''
        # this is a set of subgraphs for one task.
        batched_graph_spt = dgl.batch(support_x)
        batched_graph_qry = dgl.batch(query_x)
        batched_graph_total = dgl.batch(support_x + query_x)

        return batched_graph_total, torch.LongTensor(total_y_relative), torch.LongTensor(total_center)
        #batched_graph_spt, torch.LongTensor(support_y_relative), batched_graph_qry, torch.LongTensor(query_y_relative), torch.LongTensor(support_center), torch.LongTensor(query_center)

    def __len__(self):
        # as we have built up to batchsz of sets, you can sample some small batch size of sets.
        return self.batchsz

def collate(samples):
    # The input `samples` is a list of pairs
    #  (graph, label).
        #graphs_spt, labels_spt, graph_qry, labels_qry, center_spt, center_qry = map(list, zip(*samples))
        graphs, labels, center = map(list, zip(*samples))
        return graphs, labels, center 
        #graphs_spt, labels_spt, graph_qry, labels_qry, center_spt, center_qry