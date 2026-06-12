# 分子对接外部工具链安装说明

`deployment/requirements.txt` 只管理 Python 包。分子对接还需要三个外部工具/命令：

```text
AutoDock Vina 1.2.5
ADFRsuite 1.0
Meeko 0.5.0 / mk_prepare_ligand.py
```

其中 `Meeko` 已加入 `deployment/requirements.txt`：

```bash
pip install meeko==0.5.0
```

## 1. AutoDock Vina

推荐版本：

```text
AutoDock Vina 1.2.5
```

推荐安装方式一：conda。

```bash
conda activate medchat
conda install -c conda-forge -c bioconda vina=1.2.5 -y
which vina
vina --help
```

如果使用 conda 安装，`.env` 可以写：

```env
MOLECULAR_DOCKING_VINA=/opt/conda/envs/medchat/bin/vina
```

推荐安装方式二：下载官方 Linux 二进制文件，放到固定目录。

```bash
mkdir -p /opt/medchat/tools/autodock/vina
chmod +x /opt/medchat/tools/autodock/vina/vina
/opt/medchat/tools/autodock/vina/vina --help
```

对应 `.env`：

```env
MOLECULAR_DOCKING_ROOT=/opt/medchat/tools/autodock
MOLECULAR_DOCKING_VINA=/opt/medchat/tools/autodock/vina/vina
```

## 2. ADFRsuite

推荐版本：

```text
ADFRsuite 1.0
```

ADFRsuite 不是普通 pip 包，建议下载 Linux 版本安装包或压缩包，解压到：

```text
/opt/medchat/tools/ADFRsuite
```

需要确认 `bin` 目录里至少有：

```text
prepare_receptor
prepare_receptor.py
```

验证：

```bash
ls /opt/medchat/tools/ADFRsuite/bin/prepare_receptor*
```

对应 `.env`：

```env
MOLECULAR_DOCKING_ADFR_BIN=/opt/medchat/tools/ADFRsuite/bin
```

## 3. Meeko / mk_prepare_ligand

推荐版本：

```text
Meeko 0.5.0
```

安装：

```bash
conda activate medchat
pip install meeko==0.5.0
```

验证命令位置：

```bash
which mk_prepare_ligand.py
which mk_prepare_ligand
```

不同环境生成的命令名可能不同。哪个存在，就把哪个写入 `.env`：

```env
MOLECULAR_DOCKING_PREPARE_LIGAND=/opt/conda/envs/medchat/bin/mk_prepare_ligand.py
```

如果命令名是 `mk_prepare_ligand`：

```env
MOLECULAR_DOCKING_PREPARE_LIGAND=/opt/conda/envs/medchat/bin/mk_prepare_ligand
```

## 4. 最终 `.env` 示例

```env
MOLECULAR_DOCKING_ROOT=/opt/medchat/tools/autodock
MOLECULAR_DOCKING_VINA=/opt/conda/envs/medchat/bin/vina
MOLECULAR_DOCKING_ADFR_BIN=/opt/medchat/tools/ADFRsuite/bin
MOLECULAR_DOCKING_PREPARE_LIGAND=/opt/conda/envs/medchat/bin/mk_prepare_ligand.py
```

## 5. 健康检查

配置完成后执行：

```bash
python scripts/health_check.py --strict
```

对接工具链相关检查应全部通过：

```text
AutoDock Vina: OK
ADFRsuite: OK
Ligand Preparation: OK
```

