use std::ffi::{c_char, c_void, CStr, CString};
use std::os::windows::ffi::OsStrExt;
use std::path::{Path, PathBuf};
use std::sync::Mutex;

use pyo3::prelude::*;

const ONEOCR_DLL: &str = "oneocr.dll";
const ONEOCR_MODEL: &str = "oneocr.onemodel";
const ONNXRUNTIME_DLL: &str = "onnxruntime.dll";
const MODEL_KEY: &str = "kj)TGtrK>f]b[Piow.gU+nC@s\"\"\"\"\"\"4";
const LOAD_WITH_ALTERED_SEARCH_PATH: u32 = 0x00000008;

#[repr(C)]
#[derive(Debug, Clone, Copy)]
struct OneOcrImage {
    type_: i32,
    cols: i32,
    rows: i32,
    unknown: i32,
    step: i64,
    data_ptr: i64,
}

/// oneocr 的文字框：四个角点，共 32 字节（实测布局）。
/// `GetOcrLineBoundingBox` / `GetOcrWordBoundingBox` 通过出参返回指向它的指针。
#[repr(C)]
#[derive(Debug, Clone, Copy, Default)]
struct OcrQuad {
    x1: f32,
    y1: f32,
    x2: f32,
    y2: f32,
    x3: f32,
    y3: f32,
    x4: f32,
    y4: f32,
}

impl OcrQuad {
    fn to_vec(self) -> Vec<f32> {
        vec![
            self.x1, self.y1, self.x2, self.y2, self.x3, self.y3, self.x4, self.y4,
        ]
    }
}

/// 一行文字（含可选坐标与词级结果）。
struct LineResult {
    text: String,
    quad: Option<Vec<f32>>,
    words: Vec<WordResult>,
}

/// 一个词（含可选坐标与置信度）。
struct WordResult {
    text: String,
    quad: Option<Vec<f32>>,
    confidence: f32,
}

type CreateOcrInitOptionsFn = unsafe extern "C" fn(*mut i64) -> i64;
type SetUseModelDelayLoadFn = unsafe extern "C" fn(i64, u8) -> i64;
type CreateOcrPipelineFn = unsafe extern "C" fn(i64, i64, i64, *mut i64) -> i64;
type CreateOcrProcessOptionsFn = unsafe extern "C" fn(*mut i64) -> i64;
type SetMaxRecognitionLineCountFn = unsafe extern "C" fn(i64, i64) -> i64;
type RunOcrPipelineFn = unsafe extern "C" fn(i64, *const OneOcrImage, i64, *mut i64) -> i64;
type GetOcrLineCountFn = unsafe extern "C" fn(i64, *mut i64) -> i64;
type GetOcrLineFn = unsafe extern "C" fn(i64, i64, *mut i64) -> i64;
type GetOcrLineContentFn = unsafe extern "C" fn(i64, *mut i64) -> i64;
type ReleaseHandleFn = unsafe extern "C" fn(i64) -> i64;
// 坐标与词级接口（旧版 oneocr.dll 可能没有，因此按可选加载）
type GetOcrLineBoundingBoxFn = unsafe extern "C" fn(i64, *mut *mut OcrQuad) -> i64;
type GetOcrLineWordCountFn = unsafe extern "C" fn(i64, *mut i64) -> i64;
type GetOcrWordFn = unsafe extern "C" fn(i64, i64, *mut i64) -> i64;
type GetOcrWordContentFn = unsafe extern "C" fn(i64, *mut i64) -> i64;
type GetOcrWordBoundingBoxFn = unsafe extern "C" fn(i64, *mut *mut OcrQuad) -> i64;
type GetOcrWordConfidenceFn = unsafe extern "C" fn(i64, *mut f32) -> i64;

struct OneOcrEngine {
    library: *mut c_void,
    create_init_options: CreateOcrInitOptionsFn,
    set_use_model_delay_load: SetUseModelDelayLoadFn,
    create_pipeline: CreateOcrPipelineFn,
    create_process_options: CreateOcrProcessOptionsFn,
    set_max_recognition_line_count: SetMaxRecognitionLineCountFn,
    run_pipeline: RunOcrPipelineFn,
    get_line_count: GetOcrLineCountFn,
    get_line: GetOcrLineFn,
    get_line_content: GetOcrLineContentFn,
    release_init_options: ReleaseHandleFn,
    release_pipeline: ReleaseHandleFn,
    release_process_options: ReleaseHandleFn,
    release_result: ReleaseHandleFn,
    get_line_bounding_box: Option<GetOcrLineBoundingBoxFn>,
    get_line_word_count: Option<GetOcrLineWordCountFn>,
    get_word: Option<GetOcrWordFn>,
    get_word_content: Option<GetOcrWordContentFn>,
    get_word_bounding_box: Option<GetOcrWordBoundingBoxFn>,
    get_word_confidence: Option<GetOcrWordConfidenceFn>,
}

unsafe impl Send for OneOcrEngine {}

impl Drop for OneOcrEngine {
    fn drop(&mut self) {
        unsafe {
            FreeLibrary(self.library);
        }
    }
}

#[link(name = "kernel32")]
extern "system" {
    fn LoadLibraryExW(
        lpLibFileName: *const u16,
        hFile: *mut c_void,
        dwFlags: u32,
    ) -> *mut c_void;
    fn FreeLibrary(hLibModule: *mut c_void) -> i32;
    fn GetProcAddress(hModule: *mut c_void, lpProcName: *const u8) -> *mut c_void;
    fn SetDllDirectoryW(lpPathName: *const u16) -> i32;
}

static ENGINE: Mutex<Option<OneOcrEngine>> = Mutex::new(None);

fn has_runtime_files(runtime_dir: &Path) -> bool {
    [ONEOCR_DLL, ONEOCR_MODEL, ONNXRUNTIME_DLL]
        .iter()
        .all(|name| runtime_dir.join(name).is_file())
}

fn to_wide(path: &Path) -> Vec<u16> {
    path.as_os_str().encode_wide().chain(Some(0)).collect()
}

fn load_engine(runtime_dir: &Path) -> Result<OneOcrEngine, String> {
    let dll_path = runtime_dir.join(ONEOCR_DLL);
    if !dll_path.is_file() {
        return Err(format!("{} not found", dll_path.display()));
    }

    unsafe {
        SetDllDirectoryW(to_wide(runtime_dir).as_ptr());
    }

    let library = unsafe {
        LoadLibraryExW(
            to_wide(&dll_path).as_ptr(),
            std::ptr::null_mut(),
            LOAD_WITH_ALTERED_SEARCH_PATH,
        )
    };
    if library.is_null() {
        return Err("failed to load oneocr.dll".to_string());
    }

    macro_rules! load {
        ($name:expr, $ty:ty) => {{
            let proc = unsafe { GetProcAddress(library, concat!($name, "\0").as_ptr()) };
            if proc.is_null() {
                return Err(format!("missing export {}", $name));
            }
            unsafe { std::mem::transmute::<*mut c_void, $ty>(proc) }
        }};
    }

    // 可选导出：缺失时返回 None，让引擎仍能只提供文本（不因坐标接口缺失而整体不可用）
    macro_rules! load_optional {
        ($name:expr, $ty:ty) => {{
            let proc = unsafe { GetProcAddress(library, concat!($name, "\0").as_ptr()) };
            if proc.is_null() {
                None
            } else {
                Some(unsafe { std::mem::transmute::<*mut c_void, $ty>(proc) })
            }
        }};
    }

    Ok(OneOcrEngine {
        create_init_options: load!("CreateOcrInitOptions", CreateOcrInitOptionsFn),
        set_use_model_delay_load: load!("OcrInitOptionsSetUseModelDelayLoad", SetUseModelDelayLoadFn),
        create_pipeline: load!("CreateOcrPipeline", CreateOcrPipelineFn),
        create_process_options: load!("CreateOcrProcessOptions", CreateOcrProcessOptionsFn),
        set_max_recognition_line_count: load!("OcrProcessOptionsSetMaxRecognitionLineCount", SetMaxRecognitionLineCountFn),
        run_pipeline: load!("RunOcrPipeline", RunOcrPipelineFn),
        get_line_count: load!("GetOcrLineCount", GetOcrLineCountFn),
        get_line: load!("GetOcrLine", GetOcrLineFn),
        get_line_content: load!("GetOcrLineContent", GetOcrLineContentFn),
        release_init_options: load!("ReleaseOcrInitOptions", ReleaseHandleFn),
        release_pipeline: load!("ReleaseOcrPipeline", ReleaseHandleFn),
        release_process_options: load!("ReleaseOcrProcessOptions", ReleaseHandleFn),
        release_result: load!("ReleaseOcrResult", ReleaseHandleFn),
        get_line_bounding_box: load_optional!("GetOcrLineBoundingBox", GetOcrLineBoundingBoxFn),
        get_line_word_count: load_optional!("GetOcrLineWordCount", GetOcrLineWordCountFn),
        get_word: load_optional!("GetOcrWord", GetOcrWordFn),
        get_word_content: load_optional!("GetOcrWordContent", GetOcrWordContentFn),
        get_word_bounding_box: load_optional!("GetOcrWordBoundingBox", GetOcrWordBoundingBoxFn),
        get_word_confidence: load_optional!("GetOcrWordConfidence", GetOcrWordConfidenceFn),
        library,
    })
}

fn ensure_engine(runtime_dir: &Path) -> Result<(), String> {
    let mut guard = ENGINE.lock().map_err(|e| e.to_string())?;
    if guard.is_none() {
        *guard = Some(load_engine(runtime_dir)?);
    }
    Ok(())
}

fn run_recognize(
    runtime_dir: &Path,
    ptr: usize,
    width: usize,
    height: usize,
    stride: usize,
) -> Result<Vec<String>, String> {
    Ok(run_recognize_rich(runtime_dir, ptr, width, height, stride)?
        .into_iter()
        .map(|line| line.text)
        .collect())
}

fn run_recognize_rich(
    runtime_dir: &Path,
    ptr: usize,
    width: usize,
    height: usize,
    stride: usize,
) -> Result<Vec<LineResult>, String> {
    if !has_runtime_files(runtime_dir) {
        return Err("oneocr runtime files are missing".to_string());
    }
    ensure_engine(runtime_dir)?;

    let mut guard = ENGINE.lock().map_err(|e| e.to_string())?;
    let engine = guard.as_mut().ok_or("oneocr engine not initialized")?;

    let model_path = CString::new(
        runtime_dir
            .join(ONEOCR_MODEL)
            .to_string_lossy()
            .into_owned()
            .into_bytes(),
    )
    .map_err(|e| format!("invalid model path: {e}"))?;
    let key = CString::new(MODEL_KEY).map_err(|e| format!("invalid model key: {e}"))?;

    let mut init_options: i64 = 0;
    let mut pipeline: i64 = 0;
    let mut process_options: i64 = 0;
    let mut result: i64 = 0;

    unsafe {
        if (engine.create_init_options)(&mut init_options) != 0 {
            return Err("CreateOcrInitOptions failed".to_string());
        }
        if (engine.set_use_model_delay_load)(init_options, 0) != 0 {
            return Err("OcrInitOptionsSetUseModelDelayLoad failed".to_string());
        }
        if (engine.create_pipeline)(
            model_path.as_ptr() as i64,
            key.as_ptr() as i64,
            init_options,
            &mut pipeline,
        ) != 0
        {
            return Err("CreateOcrPipeline failed".to_string());
        }
        if (engine.create_process_options)(&mut process_options) != 0 {
            return Err("CreateOcrProcessOptions failed".to_string());
        }
        if (engine.set_max_recognition_line_count)(process_options, 1000) != 0 {
            return Err("OcrProcessOptionsSetMaxRecognitionLineCount failed".to_string());
        }

        let image = OneOcrImage {
            type_: 3,
            cols: width as i32,
            rows: height as i32,
            unknown: 0,
            step: stride as i64,
            data_ptr: ptr as i64,
        };

        if (engine.run_pipeline)(pipeline, &image, process_options, &mut result) != 0 {
            return Err("RunOcrPipeline failed".to_string());
        }

        let mut line_count: i64 = 0;
        if (engine.get_line_count)(result, &mut line_count) != 0 {
            return Err("GetOcrLineCount failed".to_string());
        }

        let mut lines = Vec::with_capacity(line_count.max(0) as usize);
        for i in 0..line_count {
            let mut line: i64 = 0;
            if (engine.get_line)(result, i, &mut line) != 0 || line == 0 {
                continue;
            }
            let mut content_ptr: i64 = 0;
            if (engine.get_line_content)(line, &mut content_ptr) != 0 || content_ptr == 0 {
                continue;
            }
            let text = CStr::from_ptr(content_ptr as *const c_char)
                .to_string_lossy()
                .trim()
                .to_string();
            if text.is_empty() {
                continue;
            }

            // 行坐标：出参是指向 oneocr 内部 OcrQuad 的指针，复制后即可脱离句柄使用
            let mut quad: Option<Vec<f32>> = None;
            if let Some(get_box) = engine.get_line_bounding_box {
                let mut box_ptr: *mut OcrQuad = std::ptr::null_mut();
                if get_box(line, &mut box_ptr) == 0 && !box_ptr.is_null() {
                    quad = Some((*box_ptr).to_vec());
                }
            }

            // 词级结果：文本 + 坐标 + 置信度
            let mut words: Vec<WordResult> = Vec::new();
            if let (Some(get_word_count), Some(get_word), Some(get_word_content)) = (
                engine.get_line_word_count,
                engine.get_word,
                engine.get_word_content,
            ) {
                let mut word_count: i64 = 0;
                if get_word_count(line, &mut word_count) == 0 {
                    for w in 0..word_count.max(0) {
                        let mut word: i64 = 0;
                        if get_word(line, w, &mut word) != 0 || word == 0 {
                            continue;
                        }
                        let mut word_content: i64 = 0;
                        if get_word_content(word, &mut word_content) != 0 || word_content == 0 {
                            continue;
                        }
                        let word_text = CStr::from_ptr(word_content as *const c_char)
                            .to_string_lossy()
                            .to_string();
                        if word_text.trim().is_empty() {
                            continue;
                        }
                        let mut word_quad: Option<Vec<f32>> = None;
                        if let Some(get_word_box) = engine.get_word_bounding_box {
                            let mut ptr_box: *mut OcrQuad = std::ptr::null_mut();
                            if get_word_box(word, &mut ptr_box) == 0 && !ptr_box.is_null() {
                                word_quad = Some((*ptr_box).to_vec());
                            }
                        }
                        let mut confidence: f32 = 0.0;
                        if let Some(get_confidence) = engine.get_word_confidence {
                            let mut value: f32 = 0.0;
                            if get_confidence(word, &mut value) == 0 {
                                confidence = value;
                            }
                        }
                        words.push(WordResult {
                            text: word_text,
                            quad: word_quad,
                            confidence,
                        });
                    }
                }
            }

            lines.push(LineResult { text, quad, words });
        }

        if result != 0 {
            (engine.release_result)(result);
        }
        if process_options != 0 {
            (engine.release_process_options)(process_options);
        }
        if pipeline != 0 {
            (engine.release_pipeline)(pipeline);
        }
        if init_options != 0 {
            (engine.release_init_options)(init_options);
        }

        Ok(lines)
    }
}

#[pyfunction]
fn available(runtime_dir: String) -> bool {
    has_runtime_files(Path::new(&runtime_dir))
}

#[pyfunction]
fn initialize(runtime_dir: String) -> bool {
    let path = PathBuf::from(runtime_dir);
    if !has_runtime_files(&path) {
        return false;
    }
    match ensure_engine(&path) {
        Ok(()) => true,
        Err(_) => false,
    }
}

#[pyfunction]
fn release() {
    if let Ok(mut guard) = ENGINE.lock() {
        *guard = None;
    }
}

#[pyfunction]
fn recognize_raw(
    runtime_dir: String,
    ptr: usize,
    width: usize,
    height: usize,
    stride: usize,
) -> PyResult<Vec<String>> {
    run_recognize(
        &PathBuf::from(runtime_dir),
        ptr,
        width,
        height,
        stride,
    )
    .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e))
}

/// 带坐标的识别结果：[(行文本, 行四点坐标 或 None, [(词文本, 词四点坐标 或 None, 置信度)])]
/// 四点坐标按 (x1,y1,x2,y2,x3,y3,x4,y4) 展开；DLL 不提供坐标时对应项为 None。
#[pyfunction]
fn recognize_lines(
    runtime_dir: String,
    ptr: usize,
    width: usize,
    height: usize,
    stride: usize,
) -> PyResult<Vec<(String, Option<Vec<f32>>, Vec<(String, Option<Vec<f32>>, f32)>)>> {
    let lines = run_recognize_rich(&PathBuf::from(runtime_dir), ptr, width, height, stride)
        .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e))?;

    Ok(lines
        .into_iter()
        .map(|line| {
            (
                line.text,
                line.quad,
                line.words
                    .into_iter()
                    .map(|word| (word.text, word.quad, word.confidence))
                    .collect(),
            )
        })
        .collect())
}

#[pymodule]
fn oneocr_engine(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(available, m)?)?;
    m.add_function(wrap_pyfunction!(initialize, m)?)?;
    m.add_function(wrap_pyfunction!(release, m)?)?;
    m.add_function(wrap_pyfunction!(recognize_raw, m)?)?;
    m.add_function(wrap_pyfunction!(recognize_lines, m)?)?;
    Ok(())
}