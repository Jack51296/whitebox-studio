"""Pinned public model files for the optional vision backends.

Every file is pinned to a repository revision and a sha256. Only licences that allow commercial use
are listed for download; gated repositories are recorded so the fetcher can report them, but they are
never requested or downloaded (weights must be obtained by the user and placed under models/<name>/).
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ModelFile:
    path: str
    sha256: str
    size: int


@dataclass(frozen=True)
class ModelSpec:
    name: str
    source: str  # "hf" | "url"
    repo: str  # Hugging Face repo id, or URL prefix for source="url"
    revision: str
    license: str
    purpose: str
    files: tuple[ModelFile, ...] = ()
    profiles: tuple[str, ...] = ()
    gated: bool = False
    commercial: bool = True
    note: str = ""
    extra: dict[str, str] = field(default_factory=dict)

    @property
    def size(self) -> int:
        return sum(f.size for f in self.files)


MODELS: tuple[ModelSpec, ...] = (
    ModelSpec(
        "transnetv2", "hf", "Sn4kehead/TransNetV2", "db6ceabeb692ec71ecc6beb6d00db67ad1412d7f",
        "MIT（原作 soCzech/TransNetV2；权重为官方 TF 权重转换的 PyTorch 版）", "切镜检测（导出 ONNX 后在主环境用 onnxruntime 推理）",
        (ModelFile("transnetv2-pytorch-weights.pth", "834b10f25ae9e1b4e4f2652fe2843bd2b1388057a435d68b7c52635578fcc04d", 30508183),),
        profiles=("default", "full")),
    ModelSpec(
        "transnetv2-code", "url", "https://raw.githubusercontent.com/soCzech/TransNetV2", "85cef72af9a916bdfd7cc94a670c9cdfbf12d1ed",
        "MIT", "TransNetV2 的 PyTorch 网络定义（导出 ONNX 用）",
        (ModelFile("inference-pytorch/transnetv2_pytorch.py",
                   "f7c1d437465579a8ec28a5add19853d2cb2755248ea4a4207678210a609428e1", 12475),), profiles=("default", "full")),
    ModelSpec(
        "centerface", "url", "https://raw.githubusercontent.com/ORB-HD/deface", "09b670db307b970cff6fad1848cf04d5f0810ec4",
        "MIT（deface 自带的 CenterFace ONNX）", "人脸检测，与 YuNet 合并打码",
        (ModelFile("deface/centerface.onnx", "09189deaaf8646c5c51a68447e3c744ea1e211798155d4728c20507b9f5aefbc", 7304518),),
        profiles=("default", "full")),
    ModelSpec(
        "grounding-dino-tiny", "hf", "IDEA-Research/grounding-dino-tiny", "a2bb814dd30d776dcf7e30523b00659f4f141c71",
        "Apache-2.0", "文本提示的主体检测框（person / bird / vehicle …）",
        (ModelFile("config.json", "eec82c5ab66e16df12a9a212e68ac011779927c2536cf9078658e35d85f0c67a", 1644),
         ModelFile("preprocessor_config.json", "8454179ba95e2ad22947835aad7b45862a601fc0055ab88bf1ee70892d3aea60", 457),
         ModelFile("special_tokens_map.json", "b6d346be366a7d1d48332dbc9fdf3bf8960b5d879522b7799ddba59e76237ee3", 125),
         ModelFile("tokenizer.json", "d241a60d5e8f04cc1b2b3e9ef7a4921b27bf526d9f6050ab90f9267a1f9e5c66", 711396),
         ModelFile("tokenizer_config.json", "d40ab645b68211910b9170d22433d43186a6ec8ee6fd10ba170524b25bf4fb56", 1237),
         ModelFile("vocab.txt", "07eced375cec144d27c900241f3e339478dec958f92fddbc551f295c992038a3", 231508),
         ModelFile("added_tokens.json", "909e96cb32d92ce728a01bc99850cbba26196d74115c17ebeb019275412588f2", 82),
         ModelFile("model.safetensors", "1a2412ef99bd74bcd3c2a246fa1e48581f8889a1300c9051974741314fc042f3", 689359096)),
        profiles=("default", "full")),
    ModelSpec(
        "sam2.1-hiera-tiny", "hf", "facebook/sam2.1-hiera-tiny", "de431c4043854a71d8101e17995dfe596bf101a5",
        "Apache-2.0", "由检测框得到主体掩码（SAM 3 权重为门控时的公开替代）",
        (ModelFile("config.json", "860aff9751b139d83a4ad7df1e5535416fded533e0ead02625edbefcb9953cce", 5695),
         ModelFile("preprocessor_config.json", "6ebf229ee259368ce4a8d4f2fe893a72b053023710853e257253939e601f583d", 683),
         ModelFile("processor_config.json", "f8a68e865cfad115c1c2763f3d93eca7b1c622da06da2a9273eb437fb2389b6d", 95),
         ModelFile("video_preprocessor_config.json", "9fccfe5f464ec38c2f236d0e6a68e95511c80c22132fc2fa4b9f7b65f24fad95", 705),
         ModelFile("model.safetensors", "48c14467e5cf9e51870511feb72c89688e82dd74523142c0538b663e193ac2a7", 155908064)),
        profiles=("default", "full")),
    ModelSpec(
        "da3-small", "hf", "depth-anything/DA3-SMALL", "e08cab65ca0ec38e7826075418411ab90cab4da3",
        "Apache-2.0", "多视图深度 + 相机位姿（几何求解，12GB 显存可用）",
        (ModelFile("config.json", "a486e29e82b7ab4a7d4cefc1ea4526cfe2ae438a572c8ca98917cfbcde7447d2", 1202),
         ModelFile("model.safetensors", "364492e38a3a06d221ac75da7f6621ada3f2361cd24fde11ba79091e9f40efcf", 137248940)),
        profiles=("default", "full")),
    ModelSpec(
        "da3-base", "hf", "depth-anything/DA3-BASE", "f4a6c9b3c95e41c82048423d3493a81ec3fa810e",
        "Apache-2.0", "多视图深度 + 相机位姿（比 SMALL 精度高）",
        (ModelFile("config.json", "5e34115ebc17bd2d8d43033c5f72e9446ac8833fd61d3fa160b7e67e0bb5b7b5", 1205),
         ModelFile("model.safetensors", "e01067dc1659613083d9145a9a2547ccdbe6ccbbf83c4fe7b3e8a4e2bdae78b5", 541518028)),
        profiles=("full",)),
    ModelSpec(
        "da3metric-large", "hf", "depth-anything/DA3METRIC-LARGE", "4010e39f3634a45bc60553321fb49fb760bd594e",
        "Apache-2.0", "单目米制深度（为几何求解提供绝对尺度）",
        (ModelFile("config.json", "a336f3e76fe375aaae17a9aed9130c9f2aa061535d317ec57dcb2f1f02e1dd53", 847),
         ModelFile("model.safetensors", "bbea5b0b3ee389849cffa7ddae89de064a90abd2b055fc5aa99aac68db324776", 1336734448)),
        profiles=("full",)),
    ModelSpec(
        "map-anything-apache", "hf", "facebook/map-anything-apache", "00f9c245bbcb60522d1ed7f9e9d88462c6e3f38a",
        "Apache-2.0", "多视图米制重建（相机 + 深度），需另装 mapanything 包",
        (ModelFile("config.json", "65701d09d99ed37a21d295f0d138978b3d584ab3bccdbcb4a2853da212b676c5", 5776),
         ModelFile("model.safetensors", "fa06c0fdccefc5048e072c85935d5789b1e36b307f3859033c17f9dcb9fd5201", 4914062480)),
        profiles=("mapanything",)),
    ModelSpec(
        "sam3", "hf", "facebook/sam3", "3c879f39826c281e95690f02c7821c4de09afae7",
        "SAM License（允许商用，禁止军事等用途）", "文本提示的主体分割与跟踪",
        gated=True, note="Hugging Face 门控仓库（需人工申请访问）。本平台不申请、不下载；获批后把权重放到 models/sam3/ 即可启用。"),
    ModelSpec(
        "vggt-1b-commercial", "hf", "facebook/VGGT-1B-Commercial", "ebb29a532abe92960eeb6903a5530f16990ef4ab",
        "VGGT Commercial License", "多视图相机与深度（备选）",
        gated=True, note="门控仓库，未申请；当前几何求解用 DA3 / MapAnything（均为公开 Apache 权重）。"),
)

EXCLUDED = (
    ("depth-anything/DA3-LARGE、DA3-GIANT", "CC BY-NC 4.0（非商用）"),
    ("facebook/map-anything（非 -apache 版）", "CC BY-NC 4.0（非商用）"),
    ("UniDepth（MegaSaM 默认深度先验）", "CC BY-NC 4.0（非商用）；MegaSaM 只接受外部 npz 导入，深度先验须换成 DA3 可商用权重"),
    ("GVHMR / WHAM / TRAM + SMPL", "非商用（SMPL 许可）"),
    ("CoTracker", "CC BY-NC 4.0（非商用）"),
    ("Ultralytics YOLO", "AGPL-3.0"),
)


def get(name: str) -> ModelSpec:
    for spec in MODELS:
        if spec.name == name:
            return spec
    raise KeyError(f"unknown model '{name}', known: {[m.name for m in MODELS]}")


def profile(name: str) -> list[ModelSpec]:
    return [m for m in MODELS if name in m.profiles]
