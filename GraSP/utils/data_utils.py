import torch
import torchvision
import torchvision.transforms as transforms
import numpy as np
import pickle


def get_transforms(dataset):
    transform_train = None
    transform_test = None
    if dataset == 'mnist':
        # transforms.Normalize((0.1307,), (0.3081,))
        t = transforms.Normalize((0.5,), (0.5,))
        transform_train = transforms.Compose([transforms.ToTensor(),t
                                              ])

        transform_test = transforms.Compose([transforms.ToTensor(),
                                             t])

    if dataset == 'cifar10':
        transform_train = transforms.Compose([
            transforms.RandomCrop(32, padding=4),
            transforms.RandomHorizontalFlip(),
            transforms.ToTensor(),
            transforms.Normalize((0.4914, 0.4822, 0.4465), (0.2023, 0.1994, 0.2010)),
        ])

        transform_test = transforms.Compose([
            transforms.ToTensor(),
            transforms.Normalize((0.4914, 0.4822, 0.4465), (0.2023, 0.1994, 0.2010)),
        ])

    if dataset == 'cifar100':
        transform_train = transforms.Compose([
            transforms.RandomCrop(32, padding=4),
            transforms.RandomHorizontalFlip(),
            transforms.ToTensor(),
            transforms.Normalize((0.5071, 0.4867, 0.4408), (0.2675, 0.2565, 0.2761)),
        ])

        transform_test = transforms.Compose([
            transforms.ToTensor(),
            transforms.Normalize((0.5071, 0.4867, 0.4408), (0.2675, 0.2565, 0.2761)),
        ])

    if dataset == 'cinic-10':
        # cinic_directory = '/path/to/cinic/directory'
        cinic_mean = [0.47889522, 0.47227842, 0.43047404]
        cinic_std = [0.24205776, 0.23828046, 0.25874835]
        transform_train = transforms.Compose([
            transforms.RandomCrop(32, padding=4),
            transforms.RandomHorizontalFlip(),
            transforms.ToTensor(),
            transforms.Normalize(cinic_mean, cinic_std)])

        transform_test = transforms.Compose([
            transforms.ToTensor(),
            transforms.Normalize(cinic_mean, cinic_std)])

    if dataset == 'tiny_imagenet':
        tiny_mean = [0.48024578664982126, 0.44807218089384643, 0.3975477478649648]
        tiny_std = [0.2769864069088257, 0.26906448510256, 0.282081906210584]
        transform_train = transforms.Compose([
            transforms.RandomCrop(64, padding=4),
            transforms.RandomHorizontalFlip(),
            transforms.ToTensor(),
            transforms.Normalize(tiny_mean, tiny_std)])

        transform_test = transforms.Compose([
            transforms.ToTensor(),
            transforms.Normalize(tiny_mean, tiny_std)])

    assert transform_test is not None and transform_train is not None, 'Error, no dataset %s' % dataset
    return transform_train, transform_test


def get_dataloader(dataset, train_batch_size, test_batch_size, num_workers=2, root='../data', percent_to_keep=1):
    transform_train, transform_test = get_transforms(dataset)
    trainset, testset = None, None
    if dataset == 'mnist':
        trainset = torchvision.datasets.MNIST(root=root, train=True, download=True, transform=transform_train)
        testset = torchvision.datasets.MNIST(root=root, train=False, download=True, transform=transform_test)

    if dataset == 'cifar10':
        trainset = torchvision.datasets.CIFAR10(root=root, train=True, download=True, transform=transform_train)
        testset = torchvision.datasets.CIFAR10(root=root, train=False, download=True, transform=transform_test)

    if dataset == 'cifar100':
        trainset = torchvision.datasets.CIFAR100(root=root, train=True, download=True, transform=transform_train)
        testset = torchvision.datasets.CIFAR100(root=root, train=False, download=True, transform=transform_test)

    if dataset == 'cinic-10':
        trainset = torchvision.datasets.ImageFolder(root + '/cinic-10/trainval', transform=transform_train)
        testset = torchvision.datasets.ImageFolder(root + '/cinic-10/test', transform=transform_test)

    if dataset == 'tiny_imagenet':
        num_workers = 16
        trainset = torchvision.datasets.ImageFolder(root + '/tiny_imagenet/train', transform=transform_train)
        testset = torchvision.datasets.ImageFolder(root + '/tiny_imagenet/val', transform=transform_test)

    assert trainset is not None and testset is not None, 'Error, no dataset %s' % dataset

    class TrainDataset(torch.utils.data.Dataset):
        def __init__(self,percent_to_remove=0):
            self.cifar10 = torchvision.datasets.CIFAR10(root=root, train=True, download=True, transform=transform_train)
            self.data = np.array(self.cifar10.data)
            self.targets = np.array(self.cifar10.targets)
            remove_list = self.remove_least_forgotten(percent_to_remove)
            self.final_data, self.final_targets = self.__remove__(remove_list)
            
        def __getitem__(self, index):
            data, target = self.final_data[index], self.final_targets[index]
            return data, target, index

        def __len__(self):
            return len(self.final_data)

        def remove_least_forgotten(self, percent):
            with open('./../cifar10_sorted_fulldata.pkl', 'rb') as f:
                forget = pickle.load(f)
            inds = forget["indices"]
            removals = int(len(self.cifar10) * percent)
            remove_list = inds[:removals]
            return(remove_list)

        def __remove__(self, remove_list):
            mask = np.ones(len(self.cifar10), dtype=bool)
            mask[remove_list] = 0
            data = self.data[mask]
            targets = self.targets[mask]
            data = np.transpose(data, (0, 3, 1, 2))
            return data, targets
    
    traindataset = TrainDataset(percent_to_remove=0)
    print(dataset.__len__())
    trainloader = torch.utils.data.DataLoader(traindataset, batch_size=train_batch_size, shuffle=True,
                                              num_workers=num_workers)
    

    class TestDataset(torch.utils.data.Dataset):
            def __init__(self):
                self.cifar10 = torchvision.datasets.CIFAR10(root=root, train=False, download=True, transform=transform_train)
                self.data = np.array(self.cifar10.data)
                self.targets = np.array(self.cifar10.targets)
                self.final_data, self.final_targets = self.__remove__([])
                
            def __getitem__(self, index):
                data, target = self.final_data[index], self.final_targets[index]
                return data, target

            def __len__(self):
                return len(self.final_data)

            def __remove__(self, remove_list):
                mask = np.ones(len(self.cifar10), dtype=bool)
                mask[remove_list] = 0
                data = self.data[mask]
                targets = self.targets[mask]
                data = np.transpose(data, (0, 3, 1, 2))
                return data, targets

    print(next(iter(trainloader))[0].shape)
    testdataset = TestDataset()

    testloader = torch.utils.data.DataLoader(testdataset, batch_size=test_batch_size, shuffle=False,
                                             num_workers=num_workers)

    return trainloader, testloader


def get_dataloader_original(dataset, train_batch_size, test_batch_size, num_workers=2, root='../data'):
    transform_train, transform_test = get_transforms(dataset)
    trainset, testset = None, None
    if dataset == 'mnist':
        trainset = torchvision.datasets.MNIST(root=root, train=True, download=True, transform=transform_train)
        testset = torchvision.datasets.MNIST(root=root, train=False, download=True, transform=transform_test)

    if dataset == 'cifar10':
        trainset = torchvision.datasets.CIFAR10(root=root, train=True, download=True, transform=transform_train)
        testset = torchvision.datasets.CIFAR10(root=root, train=False, download=True, transform=transform_test)

    if dataset == 'cifar100':
        trainset = torchvision.datasets.CIFAR100(root=root, train=True, download=True, transform=transform_train)
        testset = torchvision.datasets.CIFAR100(root=root, train=False, download=True, transform=transform_test)

    if dataset == 'cinic-10':
        trainset = torchvision.datasets.ImageFolder(root + '/cinic-10/trainval', transform=transform_train)
        testset = torchvision.datasets.ImageFolder(root + '/cinic-10/test', transform=transform_test)

    if dataset == 'tiny_imagenet':
        num_workers = 16
        trainset = torchvision.datasets.ImageFolder(root + '/tiny_imagenet/train', transform=transform_train)
        testset = torchvision.datasets.ImageFolder(root + '/tiny_imagenet/val', transform=transform_test)

    assert trainset is not None and testset is not None, 'Error, no dataset %s' % dataset

    class MyDataset(torch.utils.data.Dataset):
        def __init__(self):
            self.cifar10 = torchvision.datasets.CIFAR10(root=root, train=True, download=True, transform=transform_train)


        def __getitem__(self, index):
            data, target = self.cifar10[index]

            return data, target, index

        def __len__(self):
            return len(self.cifar10)
    dataset = MyDataset()

    trainloader = torch.utils.data.DataLoader(dataset, batch_size=train_batch_size, shuffle=True,
                                            num_workers=num_workers)
    testloader = torch.utils.data.DataLoader(testset, batch_size=test_batch_size, shuffle=False,
                                            num_workers=num_workers)

    return trainloader, testloader