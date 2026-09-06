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
            if !text.is_empty() {
                lines.push(text);
            }
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

#[pymodule]
fn oneocr_engine(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(available, m)?)?;
    m.add_function(wrap_pyfunction!(initialize, m)?)?;
    m.add_function(wrap_pyfunction!(release, m)?)?;
    m.add_function(wrap_pyfunction!(recognize_raw, m)?)?;
    Ok(())
}