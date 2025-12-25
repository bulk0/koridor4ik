# Yandex Cloud: подготовка инфраструктуры (Вариант A)

Ниже — примерный чек‑лист команд `yc` для создания окружения под VM‑деплой.

Предпосылки:
- Установлен `yc` CLI и выполнен `yc init` под вашим аккаунтом.
- Есть платежный аккаунт, создан Folder для проекта.

## 1) Папка/сервисный аккаунт/роли
```
FOLDER_ID=<ваш-folder-id>
SA_NAME=synthetic-bot-sa
yc iam service-account create --name $SA_NAME --folder-id $FOLDER_ID
SA_ID=$(yc iam service-account get --name $SA_NAME --folder-id $FOLDER_ID --format json | jq -r .id)
yc resource-manager folder add-access-binding --id $FOLDER_ID --role editor --service-account-id $SA_ID
```

## 2) VPC и подсеть
```
NET_NAME=synthetic-net
SUBNET_NAME=synthetic-subnet
yc vpc network create --name $NET_NAME --folder-id $FOLDER_ID
yc vpc subnet create --name $SUBNET_NAME --folder-id $FOLDER_ID --network-name $NET_NAME \
  --zone ru-central1-a --range 10.128.0.0/24
```

## 3) Статический публичный IP
```
yc vpc address create --name synthetic-ip --folder-id $FOLDER_ID --external-ipv4
yc vpc address list
```

## 4) Security Group
```
SG_NAME=synthetic-sg
yc vpc security-group create --name $SG_NAME --folder-id $FOLDER_ID --network-name $NET_NAME
SG_ID=$(yc vpc security-group get --name $SG_NAME --folder-id $FOLDER_ID --format json | jq -r .id)
yc vpc security-group add-rule --id $SG_ID --direction ingress --protocol tcp --port 22 --v4-cidr 0.0.0.0/0
yc vpc security-group add-rule --id $SG_ID --direction ingress --protocol tcp --port 80 --v4-cidr 0.0.0.0/0
yc vpc security-group add-rule --id $SG_ID --direction ingress --protocol tcp --port 443 --v4-cidr 0.0.0.0/0
yc vpc security-group add-rule --id $SG_ID --direction egress --protocol any --v4-cidr 0.0.0.0/0
```

## 5) ВМ
```
IMAGE_ID=$(yc compute image list --family ubuntu-2204-lts --folder-id standard-images --format json | jq -r '.[0].id')
IP_ID=$(yc vpc address get --name synthetic-ip --format json | jq -r .id)
yc compute instance create \
  --name synthetic-bot-vm \
  --folder-id $FOLDER_ID \
  --zone ru-central1-a \
  --platform standard-v3 \
  --cores 2 --memory 4 \
  --network-interface subnet-name=$SUBNET_NAME,nat-ip-address=$(yc vpc address get --name synthetic-ip --format json | jq -r .external_ipv4_address.address),security-group-ids=$SG_ID \
  --create-boot-disk image-id=$IMAGE_ID,size=20 \
  --ssh-key ~/.ssh/id_rsa.pub
```

## 6) DNS и сертификат
- Привяжите ваш домен `bot.<домен>` к публичному IP.
- На ВМ установите certbot и выпустите сертификат (см. `deploy/README.md`).

Готово. После этого используйте GitHub Actions или ручной SSH‑деплой из `deploy/README.md`.


