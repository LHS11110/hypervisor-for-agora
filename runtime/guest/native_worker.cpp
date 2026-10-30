// Incremental C/C++ compiler: declarations and JIT memory survive across cells.
#include <llvm/ExecutionEngine/Orc/LLJIT.h>
#include <clang/Interpreter/Interpreter.h>
#include <clang/Interpreter/PartialTranslationUnit.h>
#include <clang/Frontend/CompilerInstance.h>
#include <clang/AST/Decl.h>
#include <clang/Lex/Lexer.h>
#include <clang/Sema/Sema.h>
#include <clang/Sema/Scope.h>
#include <llvm/IR/Module.h>
#include <llvm/Passes/PassBuilder.h>
#include <vector>
#include <map>
#include <llvm/Support/TargetSelect.h>
#include <llvm/Support/JSON.h>
#include <llvm/Support/Error.h>
#include <unistd.h>
#include <cstdio>
#include <iostream>

int main(int argc, char **argv) {
  if (argc < 3) return 2;
  int fd = std::stoi(argv[1]);
  llvm::InitializeNativeTarget();
  llvm::InitializeNativeTargetAsmPrinter();
  llvm::InitializeNativeTargetAsmParser();
  clang::IncrementalCompilerBuilder builder;
  std::vector<const char *> compilerArgs;
  if (std::string(argv[2]) == "c") compilerArgs.push_back("-xc");
  if (argc > 3) for (int i=3;i<argc;++i) compilerArgs.push_back(argv[i]);
  else compilerArgs.push_back(std::string(argv[2]) == "c" ? "-std=c17" : "-std=c++17");
  // Keep statement entry points until we have published them. Optimizing before
  // this point can inline/remove __stmts__ and break cached repeated execution.
  compilerArgs.push_back("-Xclang"); compilerArgs.push_back("-disable-llvm-passes");
  unsigned optimization = 0;
  for (int i=3;i<argc;++i) {
    std::string flag=argv[i];
    if (flag.size()==3 && flag[0]=='-' && flag[1]=='O' && flag[2]>='0' && flag[2]<='3') optimization=flag[2]-'0';
  }
  builder.SetCompilerArgs(compilerArgs);
  auto compiler = builder.CreateCpp();
  if (!compiler) { std::cerr << llvm::toString(compiler.takeError()); return 2; }
  // Clang compiles its own REPL support before user snippets. Do not turn its
  // internal warnings into user errors when -Wall/-Wextra/-Werror are selected.
  bool warningsAsErrors = (*compiler)->getDiagnostics().getWarningsAsErrors();
  (*compiler)->getDiagnostics().setWarningsAsErrors(false);
  (*compiler)->getDiagnostics().setIgnoreAllWarnings(true);
  auto interpreter = clang::Interpreter::create(std::move(*compiler));
  if (!interpreter) { std::cerr << llvm::toString(interpreter.takeError()); return 2; }
  (*interpreter)->getCompilerInstance()->getDiagnostics().setIgnoreAllWarnings(false);
  (*interpreter)->getCompilerInstance()->getDiagnostics().setWarningsAsErrors(warningsAsErrors);
  // Keep native entry points, not source fragments to recompile on each run.
  // Initial Execute runs global initializers once. Subsequent runs call only
  // Clang's generated top-level statement functions and this cell's main.
  struct Cell {
    std::vector<void (*)()> statements;
    int (*main)(int, char **) = nullptr;
    bool mainRangeKnown = false;
    std::string mainPrefix, mainSuffix;
  };
  std::map<std::string, Cell> cells;
  unsigned long long compileCount = 0;
  std::string line;
  char ch;
  while (read(fd, &ch, 1) == 1) {
    if (ch != '\n') { line += ch; if (line.size() > 400000) return 2; continue; }
    int status = 0;
    auto request = llvm::json::parse(line);
    line.clear();
    if (!request) { llvm::consumeError(request.takeError()); return 2; }
    auto code = request->getAsObject()->getString("code");
    if (!code) return 2;
    std::string source = code->str();
    auto previous = cells.find(source);
    if (previous != cells.end()) {
      for (auto statement : previous->second.statements) statement();
    } else {
      ++compileCount;
      // A main-only edit can reuse unchanged surrounding declarations, including
      // global objects in the same code cell, without reinitializing their state.
      std::string compilationSource = source;
      size_t reusedPrefix = 0, reusedSuffix = 0;
      for (const auto &[oldSource, oldCell] : cells) {
        size_t shared = oldCell.mainPrefix.size() + oldCell.mainSuffix.size();
        if (oldCell.main && oldCell.mainRangeKnown && oldCell.statements.empty() &&
            shared > reusedPrefix + reusedSuffix && source.size() >= shared &&
            source.compare(0, oldCell.mainPrefix.size(), oldCell.mainPrefix) == 0 &&
            source.compare(source.size() - oldCell.mainSuffix.size(), oldCell.mainSuffix.size(), oldCell.mainSuffix) == 0) {
          reusedPrefix = oldCell.mainPrefix.size(); reusedSuffix = oldCell.mainSuffix.size();
          compilationSource = source.substr(reusedPrefix, source.size() - shared);
        }
      }
      // Temporarily hide previous main declarations from semantic lookup. Keep
      // their machine code alive so unchanged source-cache entries remain valid.
      auto &sema = (*interpreter)->getCompilerInstance()->getSema();
      auto &mainIdentifier = sema.getASTContext().Idents.get("main");
      std::vector<clang::NamedDecl *> hiddenMain;
      for (auto decl : sema.getASTContext().getTranslationUnitDecl()->lookup(&mainIdentifier))
        if (auto fn = llvm::dyn_cast<clang::FunctionDecl>(decl); fn && fn->isMain()) hiddenMain.push_back(fn);
      auto resolverContains = [&](clang::NamedDecl *decl) {
        for (auto it = sema.IdResolver.begin(&mainIdentifier); it != sema.IdResolver.end(); ++it)
          if (*it == decl) return true;
        return false;
      };
      for (auto decl : hiddenMain) {
        if (resolverContains(decl)) sema.IdResolver.RemoveDecl(decl);
        sema.TUScope->RemoveDecl(decl);
        decl->getLexicalDeclContext()->removeDecl(decl);
      }
      auto restoreMain = [&]() {
        for (auto it = hiddenMain.rbegin(); it != hiddenMain.rend(); ++it) {
          if (!(*it)->getLexicalDeclContext()->containsDecl(*it)) (*it)->getLexicalDeclContext()->addDecl(*it);
          else (*it)->getDeclContext()->makeDeclVisibleInContext(*it);
          sema.TUScope->AddDecl(*it);
          if (!resolverContains(*it)) sema.IdResolver.AddDecl(*it);
        }
      };
      auto ptu = (*interpreter)->Parse(compilationSource);
      if (!ptu) { restoreMain(); std::cerr << llvm::toString(ptu.takeError()) << '\n'; status = 1; }
      else {
        bool mainDefined = false;
        clang::FunctionDecl *mainDecl = nullptr;
        for (auto decl : ptu->TUPart->decls())
          if (auto fn = llvm::dyn_cast<clang::FunctionDecl>(decl))
            if (fn->isMain() && fn->doesThisDeclarationHaveABody()) { mainDefined = true; mainDecl = fn; }
        if (!mainDefined) restoreMain();
        std::string mainEntry = "__agora_main_" + std::to_string(compileCount);
        std::vector<std::string> entryNames;
        if (ptu->TheModule) {
          if (mainDefined) {
            if (auto fn = ptu->TheModule->getFunction("main")) fn->setName(mainEntry);
          }
          for (auto &function : *ptu->TheModule) {
            // Clang 19 emits each group of top-level statements in an internal
            // __stmts__ function. Publish a unique entry before JIT linking so
            // its existing machine code remains directly callable afterwards.
            if (function.hasInternalLinkage() &&
                function.getName().starts_with("__stmts__")) {
              std::string name = "__agora_cell_" + std::to_string(compileCount) +
                                 "_stmt_" + std::to_string(entryNames.size());
              function.setName(name);
              function.setLinkage(llvm::GlobalValue::ExternalLinkage);
              entryNames.push_back(name);
            }
          }
        }
        if (optimization && ptu->TheModule) {
          llvm::LoopAnalysisManager loops; llvm::FunctionAnalysisManager functions;
          llvm::CGSCCAnalysisManager calls; llvm::ModuleAnalysisManager modules;
          llvm::PassBuilder passes;
          passes.registerModuleAnalyses(modules); passes.registerCGSCCAnalyses(calls);
          passes.registerFunctionAnalyses(functions); passes.registerLoopAnalyses(loops);
          passes.crossRegisterProxies(loops,functions,calls,modules);
          auto level = optimization==1 ? llvm::OptimizationLevel::O1 : optimization==2 ? llvm::OptimizationLevel::O2 : llvm::OptimizationLevel::O3;
          auto pipeline=passes.buildPerModuleDefaultPipeline(level);
          pipeline.run(*ptu->TheModule,modules);
        }
        if (auto error = (*interpreter)->Execute(*ptu)) {
          std::cerr << llvm::toString(std::move(error)) << '\n'; status = 1;
        } else {
          Cell cell;
          if (mainDecl && !mainDecl->getBeginLoc().isMacroID() && !mainDecl->getEndLoc().isMacroID()) {
            auto &sm = (*interpreter)->getCompilerInstance()->getSourceManager();
            auto &options = (*interpreter)->getCompilerInstance()->getLangOpts();
            auto end = clang::Lexer::getLocForEndOfToken(mainDecl->getEndLoc(), 0, sm, options);
            if (end.isValid() && sm.getFileID(mainDecl->getBeginLoc()) == sm.getFileID(end)) {
              size_t beginOffset = reusedPrefix + sm.getFileOffset(mainDecl->getBeginLoc());
              size_t endOffset = reusedPrefix + sm.getFileOffset(end);
              if (beginOffset <= endOffset && endOffset <= source.size()) {
                cell.mainRangeKnown = true;
                cell.mainPrefix = source.substr(0, beginOffset);
                cell.mainSuffix = source.substr(endOffset);
              }
            }
          }
          for (auto &name : entryNames) {
            auto address = (*interpreter)->getSymbolAddress(name);
            if (!address) { std::cerr << llvm::toString(address.takeError()) << '\n'; status = 1; break; }
            cell.statements.push_back(address->toPtr<void (*)()>());
          }
          if (!status && mainDefined) {
            auto address = (*interpreter)->getSymbolAddress(mainEntry);
            if (!address) { std::cerr << llvm::toString(address.takeError()) << '\n'; status = 1; }
            else cell.main = address->toPtr<int (*)(int, char **)>();
          }
          if (!status) previous = cells.emplace(source, std::move(cell)).first;
        }
      }
    }
    if (!status && previous != cells.end() && previous->second.main) {
      char name[] = "blueprint"; char *args[] = {name, nullptr};
      status = previous->second.main(1, args);
    }
    std::cout.flush(); std::cerr.flush(); fflush(nullptr);
    std::string response = "{\"exitCode\":" + std::to_string(status) + ",\"compileCount\":" + std::to_string(compileCount) + "}\n";
    if (write(fd, response.data(), response.size()) != static_cast<ssize_t>(response.size())) return 2;
  }
}
