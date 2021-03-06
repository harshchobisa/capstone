import argparse
import json
import math
import os
import sys
import torch
import torch.nn as nn
import torch.optim as optim
import numpy as np

from models.model_base import ModelBase
from tensorboardX import SummaryWriter
from tqdm import tqdm
from models.base.init_utils import weights_init
from utils.common_utils import (get_logger, makedirs, process_config, PresetLRScheduler, str_to_list)
from utils.data_utils import get_dataloader, get_dataloader_original
from utils.network_utils import get_network
from pruner.GraSP import GraSP


def get_args():
    parser = argparse.ArgumentParser()
    parser.add_argument('--target_ratio', type=float)
    parser.add_argument('--iteration', type=int)
    parser.add_argument('--reset_to', type=int)

    parser.add_argument('--network', type=str, default='vgg')
    parser.add_argument('--dataset', type=str, default='cifar10')
    parser.add_argument('--depth', type=int, default=19)

    parser.add_argument('--pretrain_model', type=str)
    parser.add_argument('--log_dir', type=str, default='runs/')


def init_config():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', type=str, required=True)
    parser.add_argument('--run', type=str, default='')
    args = parser.parse_args()
    runs = None
    if len(args.run) > 0:
        runs = args.run
    config = process_config(args.config, runs)

    return config


def init_logger(config):
    makedirs(config.summary_dir)
    makedirs(config.checkpoint_dir)

    # set logger
    path = os.path.dirname(os.path.abspath(__file__))
    path_model = os.path.join(path, 'models/base/%s.py' % config.network.lower())
    path_main = os.path.join(path, 'main_prune_non_imagenet.py')
    path_pruner = os.path.join(path, 'pruner/%s.py' % config.pruner_file)
    logger = get_logger('log', logpath=config.summary_dir + '/',
                        filepath=path_model, package_files=[path_main, path_pruner])
    logger.info(dict(config))
    writer = SummaryWriter(config.summary_dir)
    # sys.stdout = open(os.path.join(config.summary_dir, 'stdout.txt'), 'w+')
    # sys.stderr = open(os.path.join(config.summary_dir, 'stderr.txt'), 'w+')
    return logger, writer


def print_mask_information(mb, logger):
    ratios = mb.get_ratio_at_each_layer()
    logger.info('** Mask information of %s. Overall Remaining: %.2f%%' % (mb.get_name(), ratios['ratio']))
    count = 0
    for k, v in ratios.items():
        if k == 'ratio':
            continue
        logger.info('  (%d) %s: Remaining: %.2f%%' % (count, k, v))
        count += 1


def save_state(net, acc, epoch, loss, config, ckpt_path, is_best=False):
    print('Saving..')
    state = {
        'net': net,
        'acc': acc,
        'epoch': epoch,
        'loss': loss,
        'args': config
    }
    if not is_best:
        torch.save(state, '%s/pruned_%s_%s%s_%d.t7' % (ckpt_path,
                                                       config.dataset,
                                                       config.network,
                                                       config.depth,
                                                       epoch))
    else:
        torch.save(state, '%s/finetuned_%s_%s%s_best.t7' % (ckpt_path,
                                                            config.dataset,
                                                            config.network,
                                                            config.depth))


def train(net, loader, optimizer, criterion, lr_scheduler, epoch, writer, iteration, indicies_list, accuracies_list):
    print('\nEpoch: %d' % epoch)
    net.train()
    train_loss = 0
    correct = 0
    total = 0

    lr_scheduler(optimizer, epoch)
    desc = ('[LR=%s] Loss: %.3f | Acc: %.3f%% (%d/%d)' %
            (lr_scheduler.get_lr(optimizer), 0, 0, correct, total))

    writer.add_scalar('iter_%d/train/lr' % iteration, lr_scheduler.get_lr(optimizer), epoch)

    prog_bar = tqdm(enumerate(loader), total=len(loader), desc=desc, leave=True)

    epoch_accuracies_list = []
    epoch_indicies_list = []
    for batch_idx, (inputs, targets, indicies) in prog_bar:

        inputs, targets = inputs.cuda(), targets.cuda()
        inputs = inputs.float()
        optimizer.zero_grad()
        outputs = net(inputs)

        loss = criterion(outputs, targets)
        # import pdb; pdb.set_trace()
        loss.backward()
        optimizer.step()


        train_loss += loss.item()
        _, predicted = outputs.max(1)


        accuracies = predicted == targets
        epoch_indicies_list.extend(indicies.tolist())
        epoch_accuracies_list.extend(accuracies.tolist())

        total += targets.size(0)
        correct += predicted.eq(targets).sum().item()

        desc = ('[LR=%s] Loss: %.3f | Acc: %.3f%% (%d/%d)' %
                (lr_scheduler.get_lr(optimizer), train_loss / (batch_idx + 1), 100. * correct / total, correct, total))
        prog_bar.set_description(desc, refresh=True)

    writer.add_scalar('iter_%d/train/loss' % iteration, train_loss / (batch_idx + 1), epoch)
    writer.add_scalar('iter_%d/train/acc' % iteration, 100. * correct / total, epoch)

    indicies_list = np.vstack((indicies_list, epoch_indicies_list))
    accuracies_list = np.vstack((accuracies_list, epoch_accuracies_list))

    return(indicies_list, accuracies_list)


def test(net, loader, criterion, epoch, writer, iteration):
    net.eval()
    test_loss = 0
    correct = 0
    total = 0
    desc = ('Loss: %.3f | Acc: %.3f%% (%d/%d)'
            % (test_loss / (0 + 1), 0, correct, total))

    prog_bar = tqdm(enumerate(loader), total=len(loader), desc=desc, leave=True)
    with torch.no_grad():
        for batch_idx, (inputs, targets) in prog_bar:
            inputs, targets = inputs.cuda(), targets.cuda()
            inputs = inputs.float()
            outputs = net(inputs)
            loss = criterion(outputs, targets)

            test_loss += loss.item()
            _, predicted = outputs.max(1)
            total += targets.size(0)
            correct += predicted.eq(targets).sum().item()

            desc = ('Loss: %.3f | Acc: %.3f%% (%d/%d)'
                    % (test_loss / (batch_idx + 1), 100. * correct / total, correct, total))
            prog_bar.set_description(desc, refresh=True)

    # Save checkpoint.
    acc = 100. * correct / total

    writer.add_scalar('iter_%d/test/loss' % iteration, test_loss / (batch_idx + 1), epoch)
    writer.add_scalar('iter_%d/test/acc' % iteration, 100. * correct / total, epoch)
    return acc


def train_once(mb, net, trainloader, testloader, writer, config, ckpt_path, learning_rate, weight_decay, num_epochs,
               iteration, logger):
    criterion = nn.CrossEntropyLoss()
    optimizer = optim.SGD(net.parameters(), lr=learning_rate, momentum=0.9, weight_decay=weight_decay)
    lr_schedule = {0: learning_rate,
                   int(num_epochs * 0.5): learning_rate * 0.1,
                   int(num_epochs * 0.75): learning_rate * 0.01}
    lr_scheduler = PresetLRScheduler(lr_schedule)
    best_acc = 0
    best_epoch = 0
    indicies_list=np.zeros(50000)
    accuracies_list=np.zeros(50000)

    for epoch in range(num_epochs):
        indicies_list, accuracies_list = train(net, trainloader, optimizer, criterion, lr_scheduler, epoch, writer, iteration=iteration, indicies_list=indicies_list, accuracies_list=accuracies_list)
        print(indicies_list.shape, accuracies_list.shape)
        np.save("../../gdrive/MyDrive/grasp_results/test_indicies.npy", indicies_list)
        np.save("../../gdrive/MyDrive/grasp_results/test_accuracies.npy", accuracies_list)

        test_acc = test(net, testloader, criterion, epoch, writer, iteration)
        if test_acc > best_acc:
            print('Saving..')
            state = {
                'net': net,
                'acc': test_acc,
                'epoch': epoch,
                'args': config,
                'mask': mb.masks,
                'ratio': mb.get_ratio_at_each_layer()
            }
            path = os.path.join(ckpt_path, 'finetune_%s_%s%s_r%s_it%d_best.pth.tar' % (config.dataset,
                                                                                       config.network,
                                                                                       config.depth,
                                                                                       config.target_ratio,
                                                                                       iteration))
            torch.save(state, path)
            best_acc = test_acc
            best_epoch = epoch
    logger.info('Iteration [%d], best acc: %.4f, epoch: %d' %
                (iteration, best_acc, best_epoch))


def get_exception_layers(net, exception):
    exc = []
    idx = 0
    for m in net.modules():
        if isinstance(m, (nn.Linear, nn.Conv2d)):
            if idx in exception:
                exc.append(m)
            idx += 1
    return tuple(exc)


def main(config):
    # init logger
    classes = {
        'cifar10': 10,
        'cifar100': 100,
        'mnist': 10,
        'tiny_imagenet': 200
    }
    logger, writer = init_logger(config)

    # build model
    model = get_network(config.network, config.depth, config.dataset, use_bn=config.get('use_bn', True))
    mask = None
    mb = ModelBase(config.network, config.depth, config.dataset, model)
    mb.cuda()
    if mask is not None:
        mb.register_mask(mask)
        print_mask_information(mb, logger)

    # preprocessing
    # ====================================== get dataloader ======================================
    trainloader, testloader = get_dataloader_original(config.dataset, config.batch_size, 256, 4)
    # ====================================== fetch configs ======================================
    ckpt_path = '../../gdrive/MyDrive/pruned_with_grasp/'
    num_iterations = config.iterations
    target_ratio = config.target_ratio
    normalize = config.normalize
    # ====================================== fetch exception ======================================
    exception = get_exception_layers(mb.model, str_to_list(config.exception, ',', int))
    logger.info('Exception: ')

    for idx, m in enumerate(exception):
        logger.info('  (%d) %s' % (idx, m))

    # ====================================== fetch training schemes ======================================
    ratio = 1 - (1 - target_ratio) ** (1.0 / num_iterations)
    learning_rates = str_to_list(config.learning_rate, ',', float)
    weight_decays = str_to_list(config.weight_decay, ',', float)
    training_epochs = str_to_list(config.epoch, ',', int)
    logger.info('Normalize: %s, Total iteration: %d, Target ratio: %.2f, Iter ratio %.4f.' %
                (normalize, num_iterations, target_ratio, ratio))
    logger.info('Basic Settings: ')
    for idx in range(len(learning_rates)):
        logger.info('  %d: LR: %.5f, WD: %.5f, Epochs: %d' % (idx,
                                                              learning_rates[idx],
                                                              weight_decays[idx],
                                                              training_epochs[idx]))

    # ====================================== start pruning ======================================
    iteration = 0
    for _ in range(1):
        logger.info('** Target ratio: %.4f, iter ratio: %.4f, iteration: %d/%d.' % (target_ratio,
                                                                                    ratio,
                                                                                    1,
                                                                                    num_iterations))

        mb.model.apply(weights_init)
        print("=> Applying weight initialization(%s)." % config.get('init_method', 'kaiming'))
        print("Iteration of: %d/%d" % (iteration, num_iterations))
        masks = GraSP(mb.model, ratio, trainloader, 'cuda',
                      num_classes=classes[config.dataset],
                      samples_per_class=config.samples_per_class,
                      num_iters=config.get('num_iters', 1))
        iteration = 0
        print('=> Using GraSP')
        # ========== register mask ==================
        mb.register_mask(masks)
        # ========== save pruned network ============
        logger.info('Saving..')
        state = {
            'net': mb.model,
            'acc': -1,
            'epoch': -1,
            'args': config,
            'mask': mb.masks,
            'ratio': mb.get_ratio_at_each_layer()
        }
        # path = os.path.join(ckpt_path, 'prune_%s_%s%s_r%s_it%d.pth.tar' % (config.dataset,
        #                                                                    config.network,
        #                                                                    config.depth,
        #                                                                    config.target_ratio,
        #                                                                    iteration))

        path = os.path.join(ckpt_path, 'prune_%s_%s%s_r%s_it%d.pth' % (config.dataset,
                                                                           config.network,
                                                                           config.depth,
                                                                           config.target_ratio,
                                                                           iteration))
        # torch.save(state, path)
        torch.save(mb.model.state_dict(), path)

        print("DONE SAVING")
        # return


        # ========== print pruning details ============
        logger.info('**[%d] Mask and training setting: ' % iteration)
        print_mask_information(mb, logger)
        logger.info('  LR: %.5f, WD: %.5f, Epochs: %d' %
                    (learning_rates[iteration], weight_decays[iteration], training_epochs[iteration]))

        # ========== finetuning =======================
        train_once(mb=mb,
                   net=mb.model,
                   trainloader=trainloader,
                   testloader=testloader,
                   writer=writer,
                   config=config,
                   ckpt_path=ckpt_path,
                   learning_rate=learning_rates[iteration],
                   weight_decay=weight_decays[iteration],
                   num_epochs=training_epochs[iteration],
                   iteration=iteration,
                   logger=logger)


if __name__ == '__main__':
    config = init_config()
    main(config)
